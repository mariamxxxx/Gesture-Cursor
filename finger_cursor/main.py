"""
main.py — Entry point for FingerCursor.

Step 6: tray icon + Ctrl+Shift+F hotkey toggle.

Thread model
─────────────
  Main thread   : pystray icon.run() (Windows message loop)
  Thread Overlay: Tkinter fullscreen overlay (daemon)
  Thread Tracker: MediaPipe webcam loop (daemon)
  Thread Pipeline: tracking_loop — reads tracker queue, moves cursor (daemon)
"""

import math
import queue
import threading
import time
import logging
import sys

import pystray
from PIL import Image, ImageDraw
import keyboard

from tracker import HandTracker, LM_INDEX_TIP
from cursor_mover import CursorMover
from overlay import Overlay
from gesture import (
    GestureDetector,
    LeftClick, RightClick, DoubleClick,
    DragStart, DragEnd, Scroll,
)
from settings import SettingsPanel, load_config, save_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("finger_cursor.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Coordinate helpers
# ──────────────────────────────────────────────────────────────────────

def _edge_curve(t: float, power: float = 1.6) -> float:
    """Non-linear curve that stretches positions toward screen edges.
    power > 1 amplifies values near 0 and 1 so corners are easier to reach
    without needing to push the finger to the extreme camera boundary."""
    if t <= 0.5:
        return 0.5 * (2.0 * t) ** power
    return 1.0 - 0.5 * (2.0 * (1.0 - t)) ** power


class Smoother:
    def __init__(self, alpha: float = 0.4):
        self.alpha = alpha
        self._sx: float | None = None
        self._sy: float | None = None

    def update(self, x: float, y: float) -> tuple[float, float]:
        if self._sx is None:
            self._sx, self._sy = x, y
            return x, y
        # Adaptive alpha: boost responsiveness when hand moves fast so the
        # cursor keeps up, drop back to base alpha when hand is still so
        # jitter is suppressed.
        raw_dist = math.hypot(x - self._sx, y - self._sy)
        alpha = min(1.0, self.alpha + raw_dist * 5.0)
        self._sx = alpha * x + (1 - alpha) * self._sx
        self._sy = alpha * y + (1 - alpha) * self._sy
        return self._sx, self._sy

    def reset(self):
        self._sx = self._sy = None


class CoordMapper:
    def __init__(self, screen_w: int, screen_h: int, margin: float = 0.08):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.margin   = margin

    def map(self, norm_x: float, norm_y: float) -> tuple[int, int]:
        m  = self.margin
        rx = max(0.0, min(1.0, (norm_x - m) / (1 - 2 * m)))
        ry = max(0.0, min(1.0, (norm_y - m) / (1 - 2 * m)))
        # Stretch toward edges so corners are reachable without
        # requiring the finger to reach the very edge of the camera frame.
        rx = _edge_curve(rx)
        ry = _edge_curve(ry)
        return int(rx * self.screen_w), int(ry * self.screen_h)


# ──────────────────────────────────────────────────────────────────────
# Gesture dispatch
# ──────────────────────────────────────────────────────────────────────

def _dispatch_gestures(events, mover: CursorMover, overlay: Overlay | None):
    for ev in events:
        if isinstance(ev, LeftClick):
            mover.click()
            log.info("Gesture: left_click")
            if overlay: overlay.flash("green")

        elif isinstance(ev, RightClick):
            mover.right_click()
            log.info("Gesture: right_click")
            if overlay: overlay.flash("yellow")

        elif isinstance(ev, DoubleClick):
            mover.double_click()
            log.info("Gesture: double_click")
            if overlay: overlay.flash("cyan")

        elif isinstance(ev, DragStart):
            mover.mouse_down()
            log.info("Gesture: drag_start")
            if overlay: overlay.flash("orange")

        elif isinstance(ev, DragEnd):
            mover.mouse_up()
            log.info("Gesture: drag_end")
            if overlay: overlay.flash("white")

        elif isinstance(ev, Scroll):
            mover.scroll(ev.delta)
            log.info("Gesture: scroll delta=%d", ev.delta)
            if overlay: overlay.flash("dodger blue")


# ──────────────────────────────────────────────────────────────────────
# Pipeline loop (runs in its own thread when tracking is active)
# ──────────────────────────────────────────────────────────────────────

def tracking_loop(
    cam_index:    int              = 0,
    show_debug:   bool             = False,
    margin:       float            = 0.15,
    smooth_alpha: float            = 0.4,
    max_speed:    int              = 80,
    overlay:      Overlay | None   = None,
    stop_event:   threading.Event | None = None,
):
    mover    = CursorMover(max_speed=max_speed)
    sw, sh   = mover.screen_size()
    mapper   = CoordMapper(sw, sh, margin=margin)
    smoother = Smoother(alpha=smooth_alpha)
    gesture  = GestureDetector()
    tracker  = HandTracker(cam_index=cam_index, show_debug=show_debug)
    tracker.start()

    log.info("Pipeline: screen %dx%d  margin=%.0f%%  α=%.2f",
             sw, sh, margin * 100, smooth_alpha)

    last_seen = time.monotonic()

    try:
        while not (stop_event and stop_event.is_set()):
            try:
                data = tracker.queue.get(timeout=0.1)
            except queue.Empty:
                continue

            hands   = data["landmarks"]
            frame_w = data["frame_w"]
            frame_h = data["frame_h"]

            events = gesture.update(hands, frame_w, frame_h)
            _dispatch_gestures(events, mover, overlay)

            if not hands:
                if time.monotonic() - last_seen > 0.5:
                    smoother.reset()
                    mover.reset_speed_cap()
                continue

            last_seen = time.monotonic()

            tip = hands[0][LM_INDEX_TIP]
            sx, sy = smoother.update(tip["x"], tip["y"])
            screen_x, screen_y = mapper.map(sx, sy)
            actual_x, actual_y = mover.move_to(screen_x, screen_y)

            if overlay:
                overlay.update(actual_x, actual_y)

    except Exception:
        log.exception("Pipeline: unhandled exception.")
    finally:
        try:
            mover.mouse_up()
        except Exception:
            pass
        tracker.stop()
        log.info("Pipeline: stopped.")


def _pipeline_with_restart(stop_event: threading.Event, **kwargs):
    """Run tracking_loop and restart it automatically if it crashes."""
    while not stop_event.is_set():
        tracking_loop(stop_event=stop_event, **kwargs)
        if not stop_event.is_set():
            log.warning("Pipeline: restarting in 2 s after unexpected exit.")
            time.sleep(2.0)


# ──────────────────────────────────────────────────────────────────────
# Tray icon image
# ──────────────────────────────────────────────────────────────────────

def _make_icon(active: bool) -> Image.Image:
    size  = 64
    img   = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)

    bg    = (72, 199, 116) if active else (130, 130, 130)   # green / grey
    draw.ellipse([2, 2, 62, 62], fill=bg)

    # Simple finger silhouette: rounded rectangle + oval tip
    draw.rectangle([24, 20, 40, 54], fill=(255, 255, 255, 210), outline=None)
    draw.ellipse([22, 8, 42, 30], fill=(255, 255, 255, 230))

    # Small dot at fingertip when active
    if active:
        draw.ellipse([28, 10, 36, 18], fill=(72, 199, 116))

    return img


# ──────────────────────────────────────────────────────────────────────
# App controller
# ──────────────────────────────────────────────────────────────────────

class FingerCursorApp:
    HOTKEY = "ctrl+shift+f"

    def __init__(
        self,
        cam_index:    int   = 0,
        show_debug:   bool  = False,
        margin:       float = 0.15,
        smooth_alpha: float = 0.4,
        max_speed:    int   = 80,
    ):
        # Load persisted config; CLI args override only if explicitly set
        self._config = load_config()
        self._config.update({
            "cam_index":    cam_index,
            "show_debug":   show_debug,
            "margin":       margin,
            "smooth_alpha": smooth_alpha,
            "max_speed":    max_speed,
        })
        self._apply_gesture_config(self._config)

        self._active        = False
        self._lock          = threading.Lock()
        self._stop_event    = threading.Event()
        self._pipeline: threading.Thread | None = None

        self._overlay = Overlay()
        self._icon:  pystray.Icon | None = None
        self._settings_panel: SettingsPanel | None = None

    # ── Config helpers ────────────────────────────────────────────────

    @staticmethod
    def _apply_gesture_config(cfg: dict):
        """Hot-patch GestureDetector class attributes from config."""
        GestureDetector.PINCH_ENTER_R  = cfg.get("pinch_enter_r", 0.30)
        GestureDetector.DRAG_ENTER_R   = cfg.get("drag_enter_r",  0.45)
        GestureDetector.DRAG_HOLD_MS   = cfg.get("drag_hold_ms",  300)
        GestureDetector.SCROLL_MIN_R   = cfg.get("scroll_min_r",  0.006)

    def apply_settings(self, new_cfg: dict):
        """Called by SettingsPanel on Apply. Restarts pipeline with new config."""
        self._config.update(new_cfg)
        self._apply_gesture_config(new_cfg)
        # Restart pipeline so Smoother/CoordMapper/CursorMover pick up new values
        was_active = self._active
        self._disable()
        if was_active:
            self._enable()
        log.info("Settings applied.")

    # ── Lifecycle ─────────────────────────────────────────────────────

    def run(self):
        """Start the app. Blocks the calling thread (pystray main loop)."""
        self._overlay.start()
        self._overlay.hide()

        # Settings panel (shares overlay's Tkinter thread)
        self._settings_panel = SettingsPanel(
            get_root  = lambda: self._overlay._root,
            config    = self._config,
            on_apply  = self.apply_settings,
        )

        # Register global hotkey
        try:
            keyboard.add_hotkey(self.HOTKEY, self._toggle, suppress=False)
            log.info("Hotkey registered: %s", self.HOTKEY)
        except Exception as exc:
            log.warning("Hotkey registration failed: %s", exc)

        # Start tracking immediately on launch
        self._enable()

        # Build tray icon and hand control to pystray (blocks)
        self._icon = pystray.Icon(
            "FingerCursor",
            _make_icon(active=True),
            "FingerCursor — active\n" + self.HOTKEY + " to toggle",
            menu=self._build_menu(),
        )
        self._icon.run()

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                lambda _: "Disable" if self._active else "Enable",
                self._on_tray_toggle,
                default=True,
            ),
            pystray.MenuItem("Settings", self._on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

    # ── Enable / disable ──────────────────────────────────────────────

    def _enable(self):
        with self._lock:
            if self._active:
                return
            self._active = True
            self._stop_event.clear()
            self._overlay.show()
            cfg = self._config
            self._pipeline = threading.Thread(
                target=_pipeline_with_restart,
                kwargs=dict(
                    stop_event   = self._stop_event,
                    cam_index    = cfg["cam_index"],
                    show_debug   = cfg["show_debug"],
                    margin       = cfg["margin"],
                    smooth_alpha = cfg["smooth_alpha"],
                    max_speed    = cfg["max_speed"],
                    overlay      = self._overlay,
                ),
                daemon=True,
                name="Pipeline",
            )
            self._pipeline.start()
        log.info("Tracking enabled.")
        self._update_icon()

    def _disable(self):
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._stop_event.set()
            self._overlay.hide()
        log.info("Tracking disabled.")
        self._update_icon()

    def _toggle(self):
        if self._active:
            self._disable()
        else:
            self._enable()

    # ── Tray callbacks ────────────────────────────────────────────────

    def _on_tray_toggle(self, icon, item):
        self._toggle()

    def _on_settings(self, icon, item):
        if self._settings_panel:
            self._settings_panel.open()

    def _on_quit(self, icon, item):
        self._disable()
        try:
            keyboard.remove_hotkey(self.HOTKEY)
        except Exception:
            pass
        self._overlay.stop()
        icon.stop()

    # ── Icon update ───────────────────────────────────────────────────

    def _update_icon(self):
        if not self._icon:
            return
        active = self._active
        self._icon.icon  = _make_icon(active)
        self._icon.title = (
            "FingerCursor — active\n" + self.HOTKEY + " to toggle"
            if active else
            "FingerCursor — paused\n" + self.HOTKEY + " to toggle"
        )
        self._icon.update_menu()


# ──────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="FingerCursor")
    ap.add_argument("--cam",    type=int,   default=0,    help="Webcam index")
    ap.add_argument("--debug",  action="store_true",      help="Show landmark preview window")
    ap.add_argument("--margin", type=float, default=0.08, help="Dead-zone margin (0–0.4)")
    ap.add_argument("--alpha",  type=float, default=0.4,  help="Smoothing alpha")
    ap.add_argument("--speed",  type=int,   default=80,   help="Max cursor jump per frame (px)")
    args = ap.parse_args()

    print("FingerCursor starting — look for the icon in your system tray.")
    print(f"Hotkey: {FingerCursorApp.HOTKEY}  |  right-click tray icon for menu.")

    app = FingerCursorApp(
        cam_index    = args.cam,
        show_debug   = args.debug,
        margin       = args.margin,
        smooth_alpha = args.alpha,
        max_speed    = args.speed,
    )
    app.run()
