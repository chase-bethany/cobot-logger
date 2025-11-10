# main.py
import sys, time, collections, sqlite3, csv
from datetime import datetime, timedelta

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton,
    QGridLayout, QHBoxLayout, QVBoxLayout, QFrame, QComboBox,
    QInputDialog, QMessageBox, QFileDialog, QSpinBox, QDoubleSpinBox
)
import pyqtgraph as pg

# ---- RTDE API (ur-rtde 1.6.x uses top-level modules, not "ur_rtde") ----
RTDE_AVAILABLE = True
try:
    from rtde_receive import RTDEReceiveInterface
except Exception as e:
    RTDE_AVAILABLE = False
    RTDE_IMPORT_ERROR = e


# ----------------------- Small helpers & widgets -------------------------

ROBOT_IP_DEFAULT = "192.168.0.48"
RTDE_FREQ = 10.0  # Hz
PLOT_MINUTES = 60

def human_robot_mode(mode):
    mapping = {
        0: "NO CTRL", 1: "BOOT", 2: "STANDBY",
        3: "READY", 4: "RUNNING", 5: "PAUSED", 6: "ERROR"
    }
    return mapping.get(mode, f"MODE {mode}")

class RateAverager:
    """Track cycle completions via total counter and compute rolling hourly rate."""
    def __init__(self, window_hours=1.0):
        self.events = collections.deque()
        self.window = timedelta(hours=window_hours)
        self._last_total = None

    def update_from_total(self, total_count):
        if self._last_total is None:
            self._last_total = total_count
            return
        if total_count > self._last_total:
            for _ in range(total_count - self._last_total):
                self.events.append(datetime.now())
        self._last_total = total_count
        self._trim()

    def _trim(self):
        cutoff = datetime.now() - self.window
        while self.events and self.events[0] < cutoff:
            self.events.popleft()

    def hourly_rate(self):
        self._trim()
        if len(self.events) <= 1:
            hours = self.window.total_seconds() / 3600.0
            return round(len(self.events) / hours, 2)
        elapsed_h = (self.events[-1] - self.events[0]).total_seconds() / 3600.0
        return round((len(self.events) / elapsed_h) if elapsed_h > 0 else 0.0, 2)

class Tag(QLabel):
    def __init__(self, text="", ok=True):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.set_ok(ok)

    def set_ok(self, ok: bool):
        self.setStyleSheet(f"""
            QLabel {{
                padding:6px 10px; border-radius:10px; 
                background:{'#1e2e1e' if ok else '#3a1e1e'};
                color:{'#a7f3a7' if ok else '#f3a7a7'};
                font-weight:600;
            }}
        """)

# -------------------------- RTDE worker thread ---------------------------

class RTDEThread(QtCore.QObject):
    data_signal = QtCore.Signal(dict)
    state_signal = QtCore.Signal(bool, str)  # connected, msg

    def __init__(self, ip, freq):
        super().__init__()
        self.ip, self.freq = ip, freq
        self._stop = False
        self.receiver = None

    @QtCore.Slot()
    def run(self):
        if not RTDE_AVAILABLE:
            self.state_signal.emit(False, f"RTDE module import failed: {RTDE_IMPORT_ERROR}")
            return
        while not self._stop:
            try:
                self.receiver = RTDEReceiveInterface(self.ip, frequency=self.freq)
                self.state_signal.emit(True, "Connected")
                last_emit = 0
                while not self._stop:
                    try:
                        pkg = {
                            "robot_mode": self.receiver.getRobotMode(),
                            "runtime_state": self.receiver.getRuntimeState(),
                            "accepted": self.receiver.getOutputIntRegister(0),
                            "rejected": self.receiver.getOutputIntRegister(1),
                            "total":    self.receiver.getOutputIntRegister(2),
                            "timestamp": time.time()
                        }
                    except Exception:
                        raise
                    now = time.time()
                    if now - last_emit >= 0.1:  # ~10 Hz to UI
                        self.data_signal.emit(pkg)
                        last_emit = now
                    time.sleep(0.02)
            except Exception as e:
                self.state_signal.emit(False, f"RTDE reconnecting: {e}")
                time.sleep(1.5)
            finally:
                try:
                    if self.receiver:
                        self.receiver.disconnect()
                except Exception:
                    pass

    def stop(self):
        self._stop = True


# ------------------------------ Main window ------------------------------

class Dashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TriMark Cobot Logger")
        self.resize(1120, 720)
        central = QWidget(self); self.setCentralWidget(central)

        # Top controls
        self.ip_edit = QLineEdit(ROBOT_IP_DEFAULT); self.ip_edit.setFixedWidth(220)
        self.connect_btn = QPushButton("Connect")
        self.status_tag = Tag("Disconnected", ok=False)
        self.health_tag = Tag("OK", ok=True)  # new: health indicator
        top = QHBoxLayout()
        top.addWidget(QLabel("Robot IP:")); top.addWidget(self.ip_edit); top.addWidget(self.connect_btn)
        top.addStretch(1); top.addWidget(self.status_tag); top.addWidget(self.health_tag)

        # Job controls
        self.job_combo = QComboBox()
        self.new_job_btn = QPushButton("New Job")
        self.export_btn = QPushButton("Export CSV")
        jobs_box = QHBoxLayout()
        jobs_box.addWidget(QLabel("Job:"))
        jobs_box.addWidget(self.job_combo, 1)
        jobs_box.addWidget(self.new_job_btn)
        jobs_box.addWidget(self.export_btn)

        # KPI tiles
        self.accept_lbl = QLabel("0")
        self.reject_lbl = QLabel("0")
        self.total_lbl  = QLabel("0")
        self.rate_lbl   = QLabel("0 / hr")
        for w in (self.accept_lbl, self.reject_lbl, self.total_lbl, self.rate_lbl):
            w.setStyleSheet("font-size: 28px; font-weight: 700;")
        kpi_grid = QGridLayout()
        def krow(i, title, widget):
            lbl = QLabel(title); lbl.setStyleSheet("font-size:16px;")
            kpi_grid.addWidget(lbl, i, 0, alignment=Qt.AlignLeft)
            kpi_grid.addWidget(widget, i, 1, alignment=Qt.AlignLeft)
        krow(0, "Accepted:", self.accept_lbl)
        krow(1, "Rejected:", self.reject_lbl)
        krow(2, "Total:",    self.total_lbl)
        krow(3, "Hourly Rate:", self.rate_lbl)

        # Plot + target line + thresholds row
        self.plot = pg.PlotWidget(title="Hourly Rate (last 60 min)")
        self.plot.showGrid(x=True, y=True)
        self.plot.setLabel('left', 'Parts / Hour'); self.plot.setLabel('bottom', 'Time (min)')
        self.curve = self.plot.plot([])
        self.rate_history = collections.deque(maxlen=60*10)   # 10 pts/min * 60
        self.time_history = collections.deque(maxlen=60*10)

        # ---- New controls for target rate & thresholds
        self.target_rate = QSpinBox(); self.target_rate.setRange(0, 100000); self.target_rate.setValue(120)
        self.data_timeout = QDoubleSpinBox(); self.data_timeout.setDecimals(1); self.data_timeout.setRange(0.5, 3600); self.data_timeout.setValue(5.0)
        self.cycle_timeout = QDoubleSpinBox(); self.cycle_timeout.setDecimals(1); self.cycle_timeout.setRange(1.0, 36000); self.cycle_timeout.setValue(60.0)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Target/hr:")); ctrl.addWidget(self.target_rate)
        ctrl.addSpacing(16)
        ctrl.addWidget(QLabel("Data Timeout (s):")); ctrl.addWidget(self.data_timeout)
        ctrl.addSpacing(16)
        ctrl.addWidget(QLabel("Cycle Timeout (s):")); ctrl.addWidget(self.cycle_timeout)
        ctrl.addStretch(1)

        # Target rate line on plot
        self.target_line = pg.InfiniteLine(pos=self.target_rate.value(), angle=0, movable=False, pen=pg.mkPen(style=Qt.DashLine))
        self.plot.addItem(self.target_line)
        self.target_rate.valueChanged.connect(lambda v: self.target_line.setValue(v))

        # Layout
        root = QVBoxLayout(central)
        root.addLayout(top)
        root.addLayout(jobs_box)
        root.addLayout(ctrl)

        h = QHBoxLayout()
        left = QVBoxLayout()
        left.addLayout(kpi_grid)
        sep = QFrame(); sep.setFrameShape(QFrame.VLine); sep.setStyleSheet("color:#333;")
        h.addLayout(left, 0); h.addWidget(sep); h.addWidget(self.plot, 1)
        root.addLayout(h)

        # Style
        self.setStyleSheet("""
            QMainWindow{ background:#0b0f10; color:#eaeef2; }
            QLabel{ font-size:16px; }
            QLineEdit{ background:#12181a; border:1px solid #2a3438; padding:6px; border-radius:8px; color:#eaeef2;}
            QPushButton{ background:#1b2528; border:1px solid #2f3a3e; padding:8px 14px; border-radius:10px; }
            QPushButton:hover{ background:#243034; }
            QComboBox, QSpinBox, QDoubleSpinBox{
                background:#12181a; border:1px solid #2a3438; padding:6px; border-radius:8px; color:#eaeef2;
            }

            QSpinBox, QDoubleSpinBox {
                padding-right: 28px;
            }

            QSpinBox::up-button, QDoubleSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 20px;
                border-left: 1px solid #2f3a3e;
                background: #1b2528;
                border-top-right-radius: 8px;
            }

            QSpinBox::down-button, QDoubleSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 20px;
                border-left: 1px solid #2f3a3e;
                background: #1b2528;
                border-bottom-right-radius: 8px;
            }

            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
                background: #243034;
            }
                           
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                width: 10px; height: 10px;
            }
            """)

        pg.setConfigOption('background', 'k'); pg.setConfigOption('foreground', 'w')

        # DB, jobs, timers, signals
        self.db = None
        self.current_job_id = None
        self.rate_calc = RateAverager(window_hours=1.0)
        self.last_values = {"accepted": 0, "rejected": 0, "total": 0, "rate": 0.0}

        # health monitoring times
        self.last_data_ts = None
        self.last_cycle_ts = None
        self.prev_total = None

        self.connect_btn.clicked.connect(self.start_rtde)
        self.new_job_btn.clicked.connect(self.new_job)
        self.job_combo.currentIndexChanged.connect(self.select_job)
        self.export_btn.clicked.connect(self.export_csv)

        # UI update timer
        self.ui_timer = QtCore.QTimer(self); self.ui_timer.timeout.connect(self.update_plot)
        self.ui_timer.start(1000)  # 1 Hz plot update

        # Logging timer (1 Hz)
        self.log_timer = QtCore.QTimer(self); self.log_timer.timeout.connect(self.log_sample_tick)
        self.log_timer.start(1000)

        # Health timer (1 Hz)
        self.health_timer = QtCore.QTimer(self); self.health_timer.timeout.connect(self.check_health)
        self.health_timer.start(1000)

        # DB init + load jobs
        self.init_db()
        self.load_jobs()

    # ---------------------------- DB & jobs ----------------------------

    def init_db(self):
        self.db = sqlite3.connect("cobot_logger.db", check_same_thread=False)
        cur = self.db.cursor()
        cur.execute("""
          CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            started_at TEXT NOT NULL
          )
        """)
        cur.execute("""
          CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            accepted INTEGER,
            rejected INTEGER,
            total INTEGER,
            rate REAL,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
          )
        """)
        self.db.commit()

    def load_jobs(self):
        cur = self.db.cursor()
        cur.execute("SELECT id, name, started_at FROM jobs ORDER BY started_at DESC")
        rows = cur.fetchall()
        self.job_combo.blockSignals(True)
        self.job_combo.clear()
        for jid, name, started_at in rows:
            self.job_combo.addItem(f"{name}  •  {started_at[:16]}", userData=jid)
        self.job_combo.blockSignals(False)
        if rows:
            self.current_job_id = rows[0][0]
            self.job_combo.setCurrentIndex(0)
            self.load_job_data(self.current_job_id)
        else:
            self.current_job_id = None

    def new_job(self):
        name, ok = QInputDialog.getText(self, "New Job", "Job name (e.g. Line2_ValveCaps):")
        if not ok or not name.strip():
            return
        ts = datetime.now().isoformat()
        cur = self.db.cursor()
        cur.execute("INSERT INTO jobs(name, started_at) VALUES(?, ?)", (name.strip(), ts))
        self.db.commit()
        self.load_jobs()
        QMessageBox.information(self, "Job Created", f"Logging to job: {name}")

    def select_job(self, idx):
        jid = self.job_combo.itemData(idx)
        if jid is None:
            return
        self.current_job_id = jid
        self.load_job_data(jid)

    def append_sample(self, accepted, rejected, total, rate):
        if not self.current_job_id:
            return
        ts = datetime.now().isoformat()
        cur = self.db.cursor()
        cur.execute("""INSERT INTO samples(job_id, ts, accepted, rejected, total, rate)
                       VALUES(?,?,?,?,?,?)""",
                    (self.current_job_id, ts, accepted, rejected, total, float(rate)))
        if not hasattr(self, "_pending"):
            self._pending = 0
        self._pending += 1
        if self._pending >= 10:
            self.db.commit()
            self._pending = 0

    def load_job_data(self, jid):
        cur = self.db.cursor()
        cur.execute("""SELECT ts, accepted, rejected, total, rate
                       FROM samples WHERE job_id=? ORDER BY ts ASC""", (jid,))
        rows = cur.fetchall()
        self.rate_history.clear(); self.time_history.clear()
        for ts, acc, rej, tot, rate in rows[-600:]:
            try:
                self.time_history.append(datetime.fromisoformat(ts))
            except Exception:
                continue
            self.rate_history.append(rate or 0.0)
        if rows:
            _, acc, rej, tot, rate = rows[-1]
            self.accept_lbl.setText(str(acc or 0))
            self.reject_lbl.setText(str(rej or 0))
            self.total_lbl.setText(str(tot or 0))
            self.rate_lbl.setText(f"{round((rate or 0.0), 2)} / hr")
        self.update_plot()

    def export_csv(self):
        if not self.current_job_id:
            QMessageBox.warning(self, "No Job", "Select a job first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "job_export.csv", "CSV Files (*.csv)")
        if not path:
            return
        cur = self.db.cursor()
        cur.execute("""SELECT ts, accepted, rejected, total, rate
                       FROM samples WHERE job_id=? ORDER BY ts ASC""", (self.current_job_id,))
        rows = cur.fetchall()
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp","accepted","rejected","total","rate_per_hour"])
            w.writerows(rows)
        QMessageBox.information(self, "Exported", f"Saved: {path}")

    # ---------------------------- RTDE & UI ----------------------------

    def start_rtde(self):
        ip = self.ip_edit.text().strip()
        self.stop_rtde()
        if not RTDE_AVAILABLE:
            QMessageBox.critical(self, "RTDE Module Missing",
                                 f"ur-rtde not available: {RTDE_IMPORT_ERROR}")
            return
        self.worker = RTDEThread(ip, RTDE_FREQ)
        self.thread = QtCore.QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.data_signal.connect(self.on_data)
        self.worker.state_signal.connect(self.on_state)
        self.thread.start()

    def stop_rtde(self):
        try:
            if hasattr(self, "worker") and self.worker:
                self.worker.stop()
            if hasattr(self, "thread") and self.thread:
                self.thread.quit(); self.thread.wait(800)
        except Exception:
            pass

    @QtCore.Slot(bool, str)
    def on_state(self, connected, msg):
        self.status_tag.setText("Connected" if connected else msg)
        self.status_tag.set_ok(connected)
        if connected:
            self.last_data_ts = time.time()  # start health clock when we connect

    @QtCore.Slot(dict)
    def on_data(self, d):
        now = time.time()
        self.last_data_ts = now

        accepted = int(d.get("accepted") or 0)
        rejected = int(d.get("rejected") or 0)
        total    = int(d.get("total") or (accepted + rejected))

        # detect cycle completion
        if self.prev_total is None or total > self.prev_total:
            self.last_cycle_ts = now
        self.prev_total = total

        self.accept_lbl.setText(str(accepted))
        self.reject_lbl.setText(str(rejected))
        self.total_lbl.setText(str(total))

        mode = human_robot_mode(int(d.get("robot_mode") or 0))
        running = int(d.get("runtime_state") or 0) == 2  # common value for "running"
        self.status_tag.setText(f"{'RUNNING' if running else 'IDLE'} • {mode}")
        self.status_tag.set_ok(running)

        # rolling hourly rate
        self.rate_calc.update_from_total(total)
        rate = self.rate_calc.hourly_rate()
        self.rate_lbl.setText(f"{rate} / hr")

        # feed history for plot
        self.rate_history.append(rate)
        self.time_history.append(datetime.now())

        # cache latest for 1Hz DB logger
        self.last_values = {"accepted": accepted, "rejected": rejected, "total": total, "rate": rate}

    def update_plot(self):
        if not self.time_history:
            return
        now = datetime.now()
        x = [-(now - t).total_seconds()/60.0 for t in self.time_history]
        self.curve.setData(x, list(self.rate_history))
        self.plot.setXRange(-PLOT_MINUTES, 0, padding=0)

    def check_health(self):
        """Alert if data stops or cycles stop for longer than thresholds."""
        now = time.time()
        msg = "OK"
        ok = True

        # Data timeout
        if self.last_data_ts is not None:
            if now - self.last_data_ts > float(self.data_timeout.value()):
                msg = f"DATA TIMEOUT > {self.data_timeout.value():.1f}s"
                ok = False

        # Cycle inactivity (only warn if we have ever seen a cycle or total)
        if ok:  # prioritize data timeout if both occur
            if self.last_cycle_ts is not None and (now - self.last_cycle_ts) > float(self.cycle_timeout.value()):
                msg = f"NO CYCLES > {self.cycle_timeout.value():.1f}s"
                ok = False

        self.health_tag.setText(msg)
        self.health_tag.set_ok(ok)

    def log_sample_tick(self):
        v = self.last_values
        self.append_sample(v["accepted"], v["rejected"], v["total"], v["rate"])

    def closeEvent(self, e):
        self.stop_rtde()
        try:
            if self.db:
                if hasattr(self, "_pending") and self._pending:
                    self.db.commit()
                self.db.close()
        except Exception:
            pass
        super().closeEvent(e)


# --------------------------------- main ----------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = Dashboard()
    w.show()
    sys.exit(app.exec())

