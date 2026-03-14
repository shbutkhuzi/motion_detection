"""Presence detection worker with ML-based prediction and visualization."""

import multiprocessing as mp
import time

import numpy as np
import tensorflow as tf
import scipy.signal as signal

from ..protocol import ProcessedCSI
from .base import BaseWorker, SENTINEL
from .eval_metrics import RunningMetrics


class PresenceWorker(BaseWorker):
    """Presence detection via ML model with live indicator."""

    def __init__(self, input_queue: mp.Queue):
        super().__init__(input_queue)

        self._CSI_HISTORY_LENGTH = 32
        self._csi_history = None
        self._MS_DIFF_TOLERANCE = 2000
        self._last_timestamp = None
        self._model = tf.keras.models.load_model("src/csi_receiver/models/model_4.16.keras")
        self._prediction = 0.0
        self._running_metrics = RunningMetrics()

    def run(self) -> None:
        """Qt event loop with timer-driven queue drain and presence indicator."""
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtWidgets

        pg.setConfigOptions(background="w", foreground="k")
        app = QtWidgets.QApplication([])
        win = pg.GraphicsLayoutWidget(
            title="Presence Detection - ML", size=(300, 200)
        )
        win.show()

        GRAY_BRUSH = pg.mkBrush("#bbbbbb")
        GRAY_PEN = pg.mkPen("#999999", width=2)
        GREEN_BRUSH = pg.mkBrush("#22dd44")
        GREEN_PEN = pg.mkPen("#118822", width=2.5)
        GLOW_PEN = pg.mkPen("#44ff66", width=5)
        INDICATOR_SIZE = 40

        indicator_plot = win.addPlot(row=0, col=0, title="Presence")
        indicator_plot.hideAxis("left")
        indicator_plot.hideAxis("bottom")
        indicator_plot.setMouseEnabled(x=False, y=False)
        indicator_plot.setXRange(-1, 1)
        indicator_plot.setYRange(-1, 1)

        glow = pg.ScatterPlotItem(
            pos=[(0, 0)], size=INDICATOR_SIZE + 12,
            pen=pg.mkPen(None), brush=pg.mkBrush(None), symbol="o",
        )
        indicator_plot.addItem(glow)
        indicator = pg.ScatterPlotItem(
            pos=[(0, 0)], size=INDICATOR_SIZE,
            pen=GRAY_PEN, brush=GRAY_BRUSH, symbol="o",
        )
        indicator_plot.addItem(indicator)

        def update():
            while True:
                try:
                    data = self._input_queue.get_nowait()
                except Exception:
                    break
                if data is SENTINEL:
                    app.quit()
                    return
                self.process(data)

            pred = self._prediction if self._prediction is not None else 0.0
            if pred == 1:
                indicator.setPen(GREEN_PEN)
                indicator.setBrush(GREEN_BRUSH)
                glow.setPen(GLOW_PEN)
                glow.setBrush(pg.mkBrush("#22dd4440"))
            else:
                indicator.setPen(GRAY_PEN)
                indicator.setBrush(GRAY_BRUSH)
                glow.setPen(pg.mkPen(None))
                glow.setBrush(pg.mkBrush(None))

        timer = pg.QtCore.QTimer()
        timer.timeout.connect(update)
        timer.start(33)

        app.exec()

    def initialize_history(self, data: ProcessedCSI) -> None:
        new_data = np.array([data.csi_wo_np])
        new_data = new_data[np.newaxis, ..., np.newaxis]
        self._csi_history = new_data

        self._prediction = 0.0

    def update_history(self, data: ProcessedCSI) -> None:
        new_data = np.array([data.csi_wo_np])
        new_data = new_data[np.newaxis, ..., np.newaxis]
        self._csi_history = np.concatenate([self._csi_history, new_data], axis=1)

        if self._csi_history.shape[1] > self._CSI_HISTORY_LENGTH:
            self._csi_history = self._csi_history[:, -self._CSI_HISTORY_LENGTH:]

    def model_predict(self) -> None:
        # print(self._csi_history.shape)

        # frequencies, psd = signal.welch(self._csi_history, fs=10, axis=-3, nperseg=32)
        # psd_log = np.log10(np.maximum(self._csi_history, np.finfo(np.float32).eps))
        csi_log = np.log10(
            np.maximum(np.abs(self._csi_history).astype(np.float32), np.finfo(np.float32).eps)
        )
        csi_log_min = np.min(csi_log)
        csi_log_max = np.max(csi_log)
        csi_log_normalized = (csi_log - csi_log_min) / (csi_log_max - csi_log_min + np.finfo(np.float32).eps)
        csi_log_normalized = csi_log_normalized.astype(np.float32)

        # print(psd_log_normalized.shape)

        self._prediction = self._model.predict(csi_log_normalized, verbose=0)[0][0]
        self._prediction = 1 if self._prediction > 0.5 else 0.0

        # print("--------------> Presence: ", self._prediction)


    def process(self, data: ProcessedCSI) -> None:
        """Process CSI for presence detection."""

        try:

            now = time.perf_counter()
            
            if self._last_timestamp is None or (now - self._last_timestamp) > self._MS_DIFF_TOLERANCE / 1000.0:
                self.initialize_history(data)
            else:
                if self._csi_history is None:
                    self.initialize_history(data)
                else:
                    # if len(self._mahalanobis_dist_history) == self._MAHALANOBIS_DIST_HISTORY_LENGTH:
                    #     dist = self.calculate_mahalanobis_dist(data.magnitude_db_wo_np)
                    #     if self._mahalanobis_dist_stats.get("upper_limit") < dist:
                    #         print(f"New data point with Mahalanobis distance {dist} is rejected")
                    #         return

                    self.update_history(data)

            if self._csi_history.shape[1] == self._CSI_HISTORY_LENGTH:
                self.model_predict()

                if data.label is not None:
                    self._running_metrics.update(data.label, self._prediction)
                    print("-----------------> ML F1 Score: ", self._running_metrics.get_f1_score())
                    print("-----------------> ML Accuracy: ", self._running_metrics.get_accuracy())
                    print("-----------------> ML Precision: ", self._running_metrics.get_precision())
                    print("-----------------> ML Recall: ", self._running_metrics.get_recall())

        finally:
            self._last_timestamp = now
