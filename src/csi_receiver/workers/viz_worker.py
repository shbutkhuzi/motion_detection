"""Visualization worker: PyQtGraph waterfall and line plots."""

import multiprocessing as mp
from collections import deque

import numpy as np

from ..config import DB_MAX, DB_MIN, N_SUBCARRIERS, WATERFALL_SIZE
from ..protocol import ProcessedCSI
from .base import BaseWorker, SENTINEL

# Number of trailing samples for fading effect
TRAIL_LENGTH = 20


class VizWorker(BaseWorker):
    """PyQtGraph worker: waterfall + magnitude/phase line plots."""

    def __init__(self, input_queue: mp.Queue):
        super().__init__(input_queue)
        self._pending: deque = deque(maxlen=1)  # Keep only latest

    def process(self, data: ProcessedCSI) -> None:
        """Queue data for UI update (VizWorker uses timer-based update)."""
        self._pending.append(data)

    def run(self) -> None:
        """Start Qt app with pyqtgraph layout and timer-driven updates."""
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtWidgets

        pg.setConfigOptions(imageAxisOrder="row-major", background="w", foreground="k")
        app = QtWidgets.QApplication([])
        win = pg.GraphicsLayoutWidget(title="CSI Receiver", size=(800, 600))
        win.show()

        cmap = None
        try:
            cmap = pg.colormap.get("CET-R2")
        except Exception:
            pass

        # Left: waterfall (row 0-1, col 0)
        waterfall_plot = win.addPlot(row=0, col=0, rowspan=2, title="Waterfall (|CSI| dB)")
        waterfall_plot.setLabel("left", "Time")
        waterfall_plot.setLabel("bottom", "Subcarrier")
        img = pg.ImageItem(axisOrder="row-major")
        img.setAutoLevels(False)  # Prevent setImage from overwriting levels; colorbar controls them
        img.setLevels([DB_MIN, DB_MAX])
        if cmap is not None:
            img.setLookupTable(cmap.getLookupTable(nPts=256))
        waterfall_plot.addItem(img)

        # Colorbar/legend below waterfall (default range from config)
        colorbar = pg.ColorBarItem(
            values=(DB_MIN, DB_MAX),
            colorMap=cmap or "viridis",
            label="dB",
            limits=(DB_MIN, DB_MAX),
            orientation="horizontal",
        )
        colorbar.setImageItem(img, insert_in=waterfall_plot)

        # Right: magnitude and phase
        mag_plot = win.addPlot(row=0, col=1, title="Magnitude (dB) vs Subcarriers")
        mag_plot.setLabel("bottom", "Subcarrier")
        mag_plot.setYRange(DB_MIN, DB_MAX)

        def on_levels_changed(_cb):
            """Sync waterfall and magnitude plot Y range with colorbar sliders."""
            low, high = _cb.levels()
            img.setLevels([low, high])
            mag_plot.setYRange(low, high)

        colorbar.sigLevelsChanged.connect(on_levels_changed)

        phase_plot = win.addPlot(row=1, col=1, title="Phase (rad) vs Subcarriers")
        phase_plot.setLabel("bottom", "Subcarrier")

        waterfall_data = np.full(
            (WATERFALL_SIZE, N_SUBCARRIERS), DB_MIN, dtype=np.float32
        )
        subcarrier_idx = np.arange(N_SUBCARRIERS, dtype=np.float32)

        # Trailing samples for fading effect
        mag_trail: deque = deque(maxlen=TRAIL_LENGTH)
        phase_trail: deque = deque(maxlen=TRAIL_LENGTH)
        mag_curves = [mag_plot.plot(pen=pg.mkPen("#1a6e1a", width=1.5)) for _ in range(TRAIL_LENGTH)]
        phase_curves = [phase_plot.plot(pen=pg.mkPen("#0055aa", width=1.5)) for _ in range(TRAIL_LENGTH)]

        def update():
            # Drain queue, keep latest
            while True:
                try:
                    data = self._input_queue.get_nowait()
                except Exception:
                    break
                if data is SENTINEL:
                    app.quit()
                    return
                self._pending.append(data)

            if not self._pending:
                return

            data = self._pending[-1]

            # Waterfall: roll and insert new row at top
            waterfall_data[1:] = waterfall_data[:-1]
            waterfall_data[0] = data.magnitude_db
            img.setImage(waterfall_data, rect=(0, 0, N_SUBCARRIERS, WATERFALL_SIZE))

            # Add to trails for fading
            mag_trail.append(data.magnitude_db.copy())
            phase_trail.append(data.phase_rad.copy())

            n_trail = len(mag_trail)
            # Draw trails with fading opacity (oldest = most transparent)
            for i, (mag_row, phase_row) in enumerate(zip(mag_trail, phase_trail)):
                alpha = (i + 1) / n_trail
                mag_curves[i].setData(subcarrier_idx, mag_row)
                mag_curves[i].setOpacity(alpha)
                phase_curves[i].setData(subcarrier_idx, phase_row)
                phase_curves[i].setOpacity(alpha)

            # Hide unused curves
            for j in range(n_trail, TRAIL_LENGTH):
                mag_curves[j].setData([], [])
                phase_curves[j].setData([], [])

        timer = pg.QtCore.QTimer()
        timer.timeout.connect(update)
        timer.start(33)  # ~30 fps

        app.exec()
