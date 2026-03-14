"""Presence detection worker with statistical analysis and live histogram."""

import multiprocessing as mp
import time
import numpy as np
from sklearn.covariance import LedoitWolf

from ..protocol import ProcessedCSI
from .base import BaseWorker, SENTINEL
from .eval_metrics import RunningMetrics

HIST_BINS = 200
HIST_RANGE_MIN = 0.0
HIST_RANGE_MAX = 100.0


class StatPresenceWorker(BaseWorker):
    """Statistics for presence detection. Process CSI data to detect presence."""

    def __init__(self, input_queue: mp.Queue):
        super().__init__(input_queue)
        self._csi_history = None
        self._rssi_history = None
        self._mahalanobis_dist_history = None
        self._last_timestamp = None
        self._MS_DIFF_TOLERANCE = 2000
        self._CSI_HISTORY_LENGTH = 10
        self._RSSI_HISTORY_LENGTH = 10
        self._MAHALANOBIS_DIST_HISTORY_LENGTH = None
        self._mahalanobis_distances = np.array([])
        self._MAHALANOBIS_DISTANCES_MAX_LENGTH = 100
        self._mahalanobis_dist_stats: dict[str, float] = {}
        self._MAHALANOBIS_UPPER_LIMIT_MIN = 5
        self._MAHALANOBIS_UPPER_LIMIT_MAX = 250
        self._rssi_var = 0.0
        self._csi_var = 0.0
        self._rssi_var_exp = 0.0
        self._csi_var_exp = 0.0
        self._csi_var_pred = 0.0
        self._rssi_var_pred = 0.0
        self._RSSI_VAR_LOW_THRESHOLD = 0.1
        self._RSSI_VAR_HIGH_THRESHOLD = 0.3
        self._CSI_VAR_LOW_THRESHOLD = 0.25
        self._CSI_VAR_HIGH_THRESHOLD = 0.35
        self._mahalanobis_dist = 0.0

        self._running_metrics = RunningMetrics()

    def run(self) -> None:
        """Qt event loop with timer-driven queue drain and histogram updates."""
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtWidgets

        pg.setConfigOptions(background="w", foreground="k")
        app = QtWidgets.QApplication([])
        win = pg.GraphicsLayoutWidget(
            title="Presence Detection - Analytical Approach", size=(600, 700)
        )
        win.show()

        # -- Top: horizontal whisker plot --
        whisker_plot = win.addPlot(row=0, col=0, colspan=3, title="Mahalanobis Distance Whisker Plot")
        whisker_plot.setXRange(HIST_RANGE_MIN, HIST_RANGE_MAX)
        whisker_plot.setYRange(-0.6, 0.6)
        whisker_plot.hideAxis("left")
        whisker_plot.setMouseEnabled(x=True, y=False)
        whisker_plot.setFixedHeight(80)

        box_pen = pg.mkPen("k", width=1.5)
        # IQR box (Q1 to Q3)
        box_item = pg.QtWidgets.QGraphicsRectItem(0, -0.3, 1, 0.6)
        box_item.setPen(box_pen)
        box_item.setBrush(pg.mkBrush("#aaccee"))
        whisker_plot.addItem(box_item)
        # Median line
        median_line = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen("r", width=3))
        whisker_plot.addItem(median_line)
        # Whisker lines (lower fence to Q1, Q3 to upper fence)
        lower_whisker_line = pg.PlotDataItem(pen=pg.mkPen("k", width=1.5))
        whisker_plot.addItem(lower_whisker_line)
        upper_whisker_line = pg.PlotDataItem(pen=pg.mkPen("k", width=1.5))
        whisker_plot.addItem(upper_whisker_line)
        # Fence ticks
        lower_fence_tick = pg.PlotDataItem(pen=pg.mkPen("k", width=1.5))
        whisker_plot.addItem(lower_fence_tick)
        upper_fence_tick = pg.PlotDataItem(pen=pg.mkPen("k", width=1.5))
        whisker_plot.addItem(upper_fence_tick)
        # Upper limit line at Q3 + 3*IQR
        upper_limit_line = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen("#8B4513", width=3))
        whisker_plot.addItem(upper_limit_line)
        # Current Mahalanobis distance marker (star)
        current_dist_marker = pg.ScatterPlotItem(
            size=10, pen=pg.mkPen("k", width=1), brush=pg.mkBrush("#ff4444"),
            symbol="star",
        )
        whisker_plot.addItem(current_dist_marker)

        # -- Middle: histogram --
        hist_plot = win.addPlot(row=1, col=0, colspan=3, title="Mahalanobis Distance Histogram")
        hist_plot.setLabel("bottom", "Mahalanobis Distance")
        hist_plot.setLabel("left", "Count")
        hist_plot.setXRange(HIST_RANGE_MIN, HIST_RANGE_MAX)

        # Link X axes so zooming/panning stays in sync
        whisker_plot.setXLink(hist_plot)

        bin_edges = np.linspace(HIST_RANGE_MIN, HIST_RANGE_MAX, HIST_BINS + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
        bar_width = bin_edges[1] - bin_edges[0]
        bar_item = pg.BarGraphItem(
            x=bin_centers,
            height=np.zeros(HIST_BINS),
            width=bar_width * 0.9,
            brush="#0055aa",
            pen=pg.mkPen("k", width=0.5),
        )
        hist_plot.addItem(bar_item)

        # -- Row 2: RSSI var and CSI var bars --
        rssi_var_plot = win.addPlot(row=2, col=0)
        rssi_var_plot.setTitle("RSSI Variance")
        rssi_var_plot.setYRange(0, 3)
        rssi_var_plot.setMouseEnabled(x=False, y=False)
        rssi_var_plot.setXRange(-0.5, 1.5)
        rssi_var_bar = pg.BarGraphItem(
            x=[0, 1], height=[0, 0], width=0.6,
            brush="#0055aa", pen=pg.mkPen("k", width=0.5),
        )
        rssi_var_plot.addItem(rssi_var_bar)
        rssi_ax = rssi_var_plot.getAxis("bottom")
        rssi_ax.setTicks([[(0, "RSSI Var"), (1, "RSSI Exp. Var")]])

        csi_var_plot = win.addPlot(row=2, col=1)
        csi_var_plot.setTitle("CSI Variance")
        csi_var_plot.setYRange(0, 15)
        csi_var_plot.setMouseEnabled(x=False, y=False)
        csi_var_plot.setXRange(-0.5, 1.5)
        csi_var_bar = pg.BarGraphItem(
            x=[0, 1], height=[0, 0], width=0.6,
            brush="#0055aa", pen=pg.mkPen("k", width=0.5),
        )
        csi_var_plot.addItem(csi_var_bar)
        csi_ax = csi_var_plot.getAxis("bottom")
        csi_ax.setTicks([[(0, "CSI Var"), (1, "CSI Exp. Var")]])

        iqr_plot = win.addPlot(row=2, col=2)
        iqr_plot.setTitle("IQR")
        iqr_plot.setLabel("left", "IQR")
        iqr_plot.hideAxis("bottom")
        iqr_plot.setYRange(0, 30)
        iqr_plot.setMouseEnabled(x=False, y=False)
        iqr_bar = pg.BarGraphItem(
            x=[0], height=[0], width=0.2,
            brush="#0055aa", pen=pg.mkPen("k", width=0.2),
        )
        iqr_plot.addItem(iqr_bar)
        iqr_plot.setXRange(-0.5, 0.5)

        # -- Row 3: indicator circles --
        GRAY_BRUSH = pg.mkBrush("#bbbbbb")
        GRAY_PEN = pg.mkPen("#999999", width=2)
        GREEN_BRUSH = pg.mkBrush("#22dd44")
        GREEN_PEN = pg.mkPen("#118822", width=2.5)
        GLOW_PEN = pg.mkPen("#44ff66", width=5)
        INDICATOR_SIZE = 32

        rssi_indicator_plot = win.addPlot(row=3, col=0)
        rssi_indicator_plot.setTitle("Presence Based on RSSI")
        rssi_indicator_plot.hideAxis("left")
        rssi_indicator_plot.hideAxis("bottom")
        rssi_indicator_plot.setMouseEnabled(x=False, y=False)
        rssi_indicator_plot.setXRange(-1, 1)
        rssi_indicator_plot.setYRange(-1, 1)
        # Glow ring behind the indicator
        rssi_glow = pg.ScatterPlotItem(
            pos=[(0, 0)], size=INDICATOR_SIZE + 12,
            pen=pg.mkPen(None), brush=pg.mkBrush(None), symbol="o",
        )
        rssi_indicator_plot.addItem(rssi_glow)
        rssi_indicator = pg.ScatterPlotItem(
            pos=[(0, 0)], size=INDICATOR_SIZE,
            pen=GRAY_PEN, brush=GRAY_BRUSH, symbol="o",
        )
        rssi_indicator_plot.addItem(rssi_indicator)

        csi_indicator_plot = win.addPlot(row=3, col=1)
        csi_indicator_plot.setTitle("Presence Based on CSI")
        csi_indicator_plot.hideAxis("left")
        csi_indicator_plot.hideAxis("bottom")
        csi_indicator_plot.setMouseEnabled(x=False, y=False)
        csi_indicator_plot.setXRange(-1, 1)
        csi_indicator_plot.setYRange(-1, 1)
        csi_glow = pg.ScatterPlotItem(
            pos=[(0, 0)], size=INDICATOR_SIZE + 12,
            pen=pg.mkPen(None), brush=pg.mkBrush(None), symbol="o",
        )
        csi_indicator_plot.addItem(csi_glow)
        csi_indicator = pg.ScatterPlotItem(
            pos=[(0, 0)], size=INDICATOR_SIZE,
            pen=GRAY_PEN, brush=GRAY_BRUSH, symbol="o",
        )
        csi_indicator_plot.addItem(csi_indicator)

        # Row stretch
        win.ci.layout.setRowStretchFactor(0, 1)
        win.ci.layout.setRowStretchFactor(1, 3)
        win.ci.layout.setRowStretchFactor(2, 3)
        win.ci.layout.setRowStretchFactor(3, 2)

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

            if len(self._mahalanobis_distances) == 0:
                return

            dists = self._mahalanobis_distances

            # Histogram
            clipped = np.clip(dists, HIST_RANGE_MIN, HIST_RANGE_MAX)
            counts, _ = np.histogram(clipped, bins=bin_edges)
            bar_item.setOpts(height=counts)

            # Whisker stats
            q1 = self._mahalanobis_dist_stats.get("q1", 0.0)
            median = self._mahalanobis_dist_stats.get("median", 0.0)
            q3 = self._mahalanobis_dist_stats.get("q3", 0.0)
            iqr = self._mahalanobis_dist_stats.get("iqr", 0.0)
            lower_fence = self._mahalanobis_dist_stats.get("lower_fence", 0.0)
            upper_fence = self._mahalanobis_dist_stats.get("upper_fence", 0.0)
            upper_limit = self._mahalanobis_dist_stats.get("upper_limit", 0.0)

            box_half = 0.25
            box_item.setRect(q1, -box_half, q3 - q1, box_half * 2)
            median_line.setValue(median)
            lower_whisker_line.setData([lower_fence, q1], [0, 0])
            upper_whisker_line.setData([q3, upper_fence], [0, 0])
            lower_fence_tick.setData([lower_fence, lower_fence], [-box_half, box_half])
            upper_fence_tick.setData([upper_fence, upper_fence], [-box_half, box_half])
            upper_limit_line.setValue(upper_limit)
            current_dist_marker.setData([self._mahalanobis_dist], [0])

            # Variance and IQR bars
            rssi_var_bar.setOpts(height=[self._rssi_var, self._rssi_var_exp])
            csi_var_bar.setOpts(height=[self._csi_var, self._csi_var_exp])
            iqr_bar.setOpts(height=[iqr])

            # Indicator circles
            if self._rssi_var_pred == 1:
                rssi_indicator.setPen(GREEN_PEN)
                rssi_indicator.setBrush(GREEN_BRUSH)
                rssi_glow.setPen(GLOW_PEN)
                rssi_glow.setBrush(pg.mkBrush("#22dd4440"))
            else:
                rssi_indicator.setPen(GRAY_PEN)
                rssi_indicator.setBrush(GRAY_BRUSH)
                rssi_glow.setPen(pg.mkPen(None))
                rssi_glow.setBrush(pg.mkBrush(None))

            if self._csi_var_pred == 1:
                csi_indicator.setPen(GREEN_PEN)
                csi_indicator.setBrush(GREEN_BRUSH)
                csi_glow.setPen(GLOW_PEN)
                csi_glow.setBrush(pg.mkBrush("#22dd4440"))
            else:
                csi_indicator.setPen(GRAY_PEN)
                csi_indicator.setBrush(GRAY_BRUSH)
                csi_glow.setPen(pg.mkPen(None))
                csi_glow.setBrush(pg.mkBrush(None))

        timer = pg.QtCore.QTimer()
        timer.timeout.connect(update)
        timer.start(33)  # ~30 fps

        app.exec()

    def calculate_mahalanobis_stats(self, dists: np.ndarray) -> tuple[float, float, float, float, float, float, float]:
        """Calculate Mahalanobis distance stats for the box plot."""
        q1 = np.percentile(dists, 25)
        median = np.percentile(dists, 50)
        q3 = np.percentile(dists, 75)
        iqr = q3 - q1
        lower_fence = q1 - 1.5 * iqr
        upper_fence = q3 + 1.5 * iqr
        upper_limit = np.min([q3 + 4 * iqr, self._MAHALANOBIS_UPPER_LIMIT_MAX])
        upper_limit = np.max([upper_limit, self._MAHALANOBIS_UPPER_LIMIT_MIN])

        return q1, median, q3, iqr, lower_fence, upper_fence, upper_limit

    def initialize_history(self, data: ProcessedCSI) -> None:
        self._csi_history = np.array([data.magnitude_db_wo_np])
        self._rssi_history = np.array([data.rssi])

        self._MAHALANOBIS_DIST_HISTORY_LENGTH = self._csi_history.shape[1]
        self._mahalanobis_dist_history = np.array([data.magnitude_db_wo_np])

        self._mahalanobis_distances = np.array([])

    def update_history(self, data: ProcessedCSI) -> None:
        self._csi_history = np.concatenate([self._csi_history, np.array([data.magnitude_db_wo_np])], axis=0)
        self._rssi_history = np.concatenate([self._rssi_history, np.array([data.rssi])], axis=0)
        self._mahalanobis_dist_history = np.concatenate([self._mahalanobis_dist_history, np.array([data.magnitude_db_wo_np])], axis=0)

        if len(self._csi_history) > self._CSI_HISTORY_LENGTH:
            self._csi_history = self._csi_history[-self._CSI_HISTORY_LENGTH:]
        if len(self._rssi_history) > self._RSSI_HISTORY_LENGTH:
            self._rssi_history = self._rssi_history[-self._RSSI_HISTORY_LENGTH:]
        if len(self._mahalanobis_dist_history) > self._MAHALANOBIS_DIST_HISTORY_LENGTH:
            self._mahalanobis_dist_history = self._mahalanobis_dist_history[-self._MAHALANOBIS_DIST_HISTORY_LENGTH:]

    def process(self, data: ProcessedCSI) -> None:
        """Process CSI for statistics. To be implemented."""

        try:

            now = time.perf_counter()
            
            if self._last_timestamp is None or (now - self._last_timestamp) > self._MS_DIFF_TOLERANCE / 1000.0:
                self.initialize_history(data)
            else:
                if self._csi_history is None:
                    self.initialize_history(data)
                else:
                    if len(self._mahalanobis_dist_history) == self._MAHALANOBIS_DIST_HISTORY_LENGTH:
                        dist = self.calculate_mahalanobis_dist(data.magnitude_db_wo_np)
                        if self._mahalanobis_dist_stats.get("upper_limit") < dist:
                            print(f"New data point with Mahalanobis distance {dist} is rejected")
                            return

                    self.update_history(data)

            if len(self._csi_history) == self._CSI_HISTORY_LENGTH:
                self.calculate_statistics()

                if data.label is not None:
                    self._running_metrics.update(data.label, self._csi_var_pred)
                    print("-----------------> Analytical F1 Score: ", self._running_metrics.get_f1_score())
                    print("-----------------> Analytical Accuracy: ", self._running_metrics.get_accuracy())
                    print("-----------------> Analytical Precision: ", self._running_metrics.get_precision())
                    print("-----------------> Analytical Recall: ", self._running_metrics.get_recall())

        finally:
            self._last_timestamp = now

    def calculate_statistics(self) -> None:
        """Calculate statistics for presence detection."""

        alpha = 0.1

        # print(self._rssi_history.shape)  (10,)
        # print(self._csi_history.shape)   (10, 51)

        weights = np.ones(self._csi_history.shape[1])
        weights[:6] = 0.1
        weights[self._csi_history.shape[1] - 6:] = 0.1

        self._rssi_var = np.var(self._rssi_history)
        self._csi_var = np.average(np.var(self._csi_history, axis=0), weights=weights)

        self._rssi_var_exp = alpha * self._rssi_var + (1 - alpha) * self._rssi_var_exp
        self._csi_var_exp = alpha * self._csi_var + (1 - alpha) * self._csi_var_exp

        if self._rssi_var_exp > self._RSSI_VAR_HIGH_THRESHOLD:
            self._rssi_var_pred = 1
        elif self._rssi_var_exp < self._RSSI_VAR_LOW_THRESHOLD:
            self._rssi_var_pred = 0

        if self._csi_var_exp > self._CSI_VAR_HIGH_THRESHOLD:
            self._csi_var_pred = 1
        elif self._csi_var_exp < self._CSI_VAR_LOW_THRESHOLD:
            self._csi_var_pred = 0

        # print(f"RSSI var: {self._rssi_var:10.6f}   RSSI exp. var: {self._rssi_var_exp:10.6f}   CSI var: {self._csi_var:10.6f}   CSI exp. var: {self._csi_var_exp:10.6f}")

    def calculate_mahalanobis_dist(self, x_new: np.ndarray) -> float:
        """Calculate Mahalanobis distance for presence detection."""

        X = self._mahalanobis_dist_history

        lw = LedoitWolf()
        lw.fit(X)

        mu = lw.location_
        Sigma_inv = lw.precision_

        diff = x_new.reshape(-1) - mu
        dist = np.sqrt(diff.T @ Sigma_inv @ diff)

        self._mahalanobis_distances = np.concatenate([self._mahalanobis_distances, np.array([dist])], axis=0)
        if len(self._mahalanobis_distances) > self._MAHALANOBIS_DISTANCES_MAX_LENGTH:
            self._mahalanobis_distances = self._mahalanobis_distances[-self._MAHALANOBIS_DISTANCES_MAX_LENGTH:]
        # print(f"Mahalanobis distance: {self._mahalanobis_distances}")

        if len(self._mahalanobis_distances):
            q1, median, q3, iqr, lower_fence, upper_fence, upper_limit = self.calculate_mahalanobis_stats(self._mahalanobis_distances)
            self._mahalanobis_dist_stats = {
                "q1": q1,
                "median": median,
                "q3": q3,
                "iqr": iqr,
                "lower_fence": lower_fence,
                "upper_fence": upper_fence,
                "upper_limit": upper_limit
            }

        self._mahalanobis_dist = dist

        return dist
