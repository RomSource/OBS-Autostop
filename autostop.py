import base64
import io
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image

try:
    import obsws_python as obs
except ImportError:
    obs = None  # app still launches; Start/Calibrate will show a clear error

DEFAULTS = {
    "host": "localhost",
    "port": "4455",
    "password": "",
    "source_name": "Video Capture Device",
    "check_interval": "2",
    "blue_hold": "45",
    "tolerance": "40",
    "match_fraction": "0.95",
    "ref_r": "20",
    "ref_g": "20",
    "ref_b": "140",
}

# Downscaled sample size used for analysis. Keep this proportional to your
# real capture resolution's aspect ratio (960x768 simplifies to 5:4, so 80x64
# keeps that ratio).
SAMPLE_SIZE = (80, 64)


# --------------------------------------------------------------------------
# Pure logic (no UI, no OBS) -- kept separate so it's easy to test/reuse
# --------------------------------------------------------------------------

def fraction_matching_blue(img: Image.Image, reference, tolerance) -> float:
    pixels = list(img.getdata())
    total = len(pixels)
    if total == 0:
        return 0.0
    rr, rg, rb = reference
    matches = sum(
        1 for (r, g, b) in pixels
        if abs(r - rr) <= tolerance and abs(g - rg) <= tolerance and abs(b - rb) <= tolerance
    )
    return matches / total


def average_color(img: Image.Image):
    pixels = list(img.getdata())
    n = len(pixels)
    r = sum(p[0] for p in pixels) / n
    g = sum(p[1] for p in pixels) / n
    b = sum(p[2] for p in pixels) / n
    return (round(r), round(g), round(b))


def get_screenshot(client, source_name: str, width: int, height: int) -> Image.Image:
    resp = client.get_source_screenshot(
        name=source_name, img_format="png", width=width, height=height, quality=-1,
    )
    header, b64data = resp.image_data.split(",", 1)
    img_bytes = base64.b64decode(b64data)
    return Image.open(io.BytesIO(img_bytes)).convert("RGB")


def beep():
    if sys.platform == "win32":
        try:
            import winsound
            winsound.Beep(880, 400)
            return
        except Exception:
            pass
    print("\a", end="", flush=True)


# GUI

class App:
    def __init__(self, root):
        self.root = root
        root.title("OBS Auto-Stop on Blue Screen")
        root.geometry("540x600")
        root.resizable(False, False)

        self.msg_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None

        self.vars = {key: tk.StringVar(value=val) for key, val in DEFAULTS.items()}

        self._build_ui()
        self.root.after(100, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # UI

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        conn = ttk.LabelFrame(self.root, text="Connection")
        conn.pack(fill="x", **pad)
        self._field(conn, "Host", "host", 0)
        self._field(conn, "Port", "port", 1)
        self._field(conn, "Password", "password", 2, show="*")
        self._field(conn, "Source name", "source_name", 3)

        settings = ttk.LabelFrame(self.root, text="Detection settings")
        settings.pack(fill="x", **pad)
        self._field(settings, "Check interval (s)", "check_interval", 0)
        self._field(settings, "Blue hold time (s)", "blue_hold", 1)
        self._field(settings, "Color tolerance", "tolerance", 2)
        self._field(settings, "Match fraction (0-1)", "match_fraction", 3)

        blue = ttk.LabelFrame(self.root, text="Reference blue color")
        blue.pack(fill="x", **pad)
        rgb_row = ttk.Frame(blue)
        rgb_row.pack(fill="x", padx=8, pady=4)
        for label, key in (("R", "ref_r"), ("G", "ref_g"), ("B", "ref_b")):
            ttk.Label(rgb_row, text=label).pack(side="left")
            ttk.Entry(rgb_row, textvariable=self.vars[key], width=5).pack(side="left", padx=(2, 10))
        self.swatch = tk.Canvas(rgb_row, width=28, height=20, bg="#142080",
                                 highlightthickness=1, highlightbackground="#888")
        self.swatch.pack(side="left", padx=6)
        ttk.Button(rgb_row, text="Calibrate from live frame", command=self.on_calibrate).pack(side="left", padx=8)
        for key in ("ref_r", "ref_g", "ref_b"):
            self.vars[key].trace_add("write", lambda *a: self._update_swatch())

        controls = ttk.Frame(self.root)
        controls.pack(fill="x", **pad)
        self.start_btn = ttk.Button(controls, text="Start Monitoring", command=self.on_start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(controls, text="Stop", command=self.on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(self.root, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=10, pady=(4, 0))

        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=14, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _field(self, parent, label, key, row, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=3)
        kwargs = {"textvariable": self.vars[key]}
        if show:
            kwargs["show"] = show
        entry = ttk.Entry(parent, **kwargs)
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=3)
        parent.columnconfigure(1, weight=1)

    def _update_swatch(self):
        try:
            r = max(0, min(255, int(self.vars["ref_r"].get())))
            g = max(0, min(255, int(self.vars["ref_g"].get())))
            b = max(0, min(255, int(self.vars["ref_b"].get())))
            self.swatch.configure(bg=f"#{r:02x}{g:02x}{b:02x}")
        except ValueError:
            pass

    # logging

    def log(self, message):
        self.msg_queue.put(("log", message))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {payload}\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "calibrated":
                    r, g, b = payload
                    self.vars["ref_r"].set(str(r))
                    self.vars["ref_g"].set(str(g))
                    self.vars["ref_b"].set(str(b))
                elif kind == "done":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    # config

    def _read_config(self):
        try:
            return {
                "host": self.vars["host"].get().strip(),
                "port": int(self.vars["port"].get()),
                "password": self.vars["password"].get(),
                "source_name": self.vars["source_name"].get().strip(),
                "interval": float(self.vars["check_interval"].get()),
                "hold": float(self.vars["blue_hold"].get()),
                "tolerance": int(self.vars["tolerance"].get()),
                "match_fraction": float(self.vars["match_fraction"].get()),
                "reference": (
                    int(self.vars["ref_r"].get()),
                    int(self.vars["ref_g"].get()),
                    int(self.vars["ref_b"].get()),
                ),
            }
        except ValueError as e:
            messagebox.showerror("Invalid settings", f"Check your numeric fields: {e}")
            return None

    def _connect(self, cfg):
        if obs is None:
            raise RuntimeError("obsws-python is not installed. Run: pip install obsws-python pillow")
        return obs.ReqClient(host=cfg["host"], port=cfg["port"], password=cfg["password"], timeout=5)

    # actions

    def on_calibrate(self):
        cfg = self._read_config()
        if cfg is None:
            return
        if not messagebox.askokcancel(
            "Calibrate",
            "Make sure the camera is currently showing the idle blue screen, then click OK.",
        ):
            return
        threading.Thread(target=self._calibrate_worker, args=(cfg,), daemon=True).start()

    def _calibrate_worker(self, cfg):
        try:
            self.log("Connecting to OBS for calibration...")
            client = self._connect(cfg)
            img = get_screenshot(client, cfg["source_name"], *SAMPLE_SIZE)
            avg = average_color(img)
            self.log(f"Sampled average color: {avg}")
            self.msg_queue.put(("calibrated", avg))
            client.disconnect()
        except Exception as e:
            self.log(f"Calibration failed: {e}")

    def on_start(self):
        cfg = self._read_config()
        if cfg is None:
            return
        self.stop_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.msg_queue.put(("status", "Connecting..."))
        self.worker_thread = threading.Thread(target=self._monitor_worker, args=(cfg,), daemon=True)
        self.worker_thread.start()

    def on_stop(self):
        self.stop_event.set()
        self.log("Stop requested by user.")
        self.msg_queue.put(("status", "Stopping..."))

    def _on_close(self):
        self.stop_event.set()
        self.root.destroy()

    def _monitor_worker(self, cfg):
        try:
            client = self._connect(cfg)
        except Exception as e:
            self.log(f"Could not connect to OBS: {e}")
            self.msg_queue.put(("status", "Connection failed"))
            self.msg_queue.put(("done", None))
            return

        self.log(f"Connected. Monitoring '{cfg['source_name']}' every {cfg['interval']}s -- "
                  f"will stop recording after {cfg['hold']}s of continuous blue screen.")
        self.msg_queue.put(("status", "Monitoring..."))

        blue_since = None
        try:
            while not self.stop_event.is_set():
                try:
                    img = get_screenshot(client, cfg["source_name"], *SAMPLE_SIZE)
                except Exception as e:
                    self.log(f"Screenshot failed: {e}")
                    time.sleep(cfg["interval"])
                    continue

                frac = fraction_matching_blue(img, cfg["reference"], cfg["tolerance"])
                is_blue = frac >= cfg["match_fraction"]
                now = time.time()

                if is_blue:
                    if blue_since is None:
                        blue_since = now
                        self.log(f"Blue screen detected ({frac:.0%} match) -- timer started")
                    elapsed = now - blue_since
                    self.msg_queue.put(("status", f"Blue held {elapsed:.0f}s / {cfg['hold']:.0f}s"))
                    if elapsed >= cfg["hold"]:
                        self.log("Blue screen sustained -- stopping recording.")
                        try:
                            status = client.get_record_status()
                            if status.output_active:
                                client.stop_record()
                                self.log("Recording stopped.")
                            else:
                                self.log("Recording was not active.")
                        except Exception as e:
                            self.log(f"Could not stop recording: {e}")
                        beep()
                        self.msg_queue.put(("status", "Stopped (blue screen detected)"))
                        break
                else:
                    if blue_since is not None:
                        self.log(f"Blue screen ended ({frac:.0%} match) -- timer reset")
                    blue_since = None
                    self.msg_queue.put(("status", "Monitoring..."))

                time.sleep(cfg["interval"])
            else:
                self.msg_queue.put(("status", "Idle"))
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
            self.msg_queue.put(("done", None))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
