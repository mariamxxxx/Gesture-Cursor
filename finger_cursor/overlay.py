"""
overlay.py — Transparent always-on-top cursor indicator overlay.

Creates a fullscreen borderless Tkinter window with a transparent background.
On Windows, the window is made click-through via ctypes so it never
intercepts mouse events (WS_EX_LAYERED | WS_EX_TRANSPARENT).

The cursor indicator is a filled circle + crosshair styled as a soft glow.

Thread-safety: update() / flash() / show() / hide() can be called from any
thread — they schedule work on the Tkinter event loop via root.after(0, ...).
"""

import sys
import tkinter as tk
import threading
import logging

log = logging.getLogger(__name__)

# Pixels painted with exactly this color become transparent on Windows.
# Using near-black (#010101) avoids accidentally erasing cursor art.
_TRANSPARENT = "#010101"

# Cursor visual constants
_RADIUS       = 16    # outer glow ring radius
_FILL_RADIUS  = 9     # inner solid dot radius
_CROSSHAIR    = 22    # half-length of crosshair lines
_LINE_W       = 2     # crosshair stroke width
_RING_W       = 2     # outer ring stroke width
_GLOW_STEPS   = 3     # concentric rings for glow effect

_COLOR_DEFAULT = "white"
_FLASH_MS      = 180   # gesture flash duration


class Overlay:
    """
    Fullscreen transparent click-through overlay.

    Typical usage from another thread:
        overlay = Overlay()
        overlay.start()          # launches Tkinter in a daemon thread
        overlay.update(x, y)     # call on every tracked frame
        overlay.flash("green")   # gesture feedback
        overlay.stop()           # tear down
    """

    def __init__(self):
        self._root:   tk.Tk     | None = None
        self._canvas: tk.Canvas | None = None
        self._thread: threading.Thread | None = None
        self._ready  = threading.Event()

        self._x = 100
        self._y = 100
        self._color = _COLOR_DEFAULT
        self._flash_job = None

        # Canvas item IDs (created in _tk_main)
        self._glow_ids: list[int] = []
        self._dot_id  = None
        self._ch_h_id = None
        self._ch_v_id = None

    # ── Public API ──────────────────────────────────────────────────────

    def start(self):
        """Launch Tkinter in a daemon thread and block until window is ready."""
        self._thread = threading.Thread(
            target=self._tk_main, daemon=True, name="Overlay"
        )
        self._thread.start()
        if not self._ready.wait(timeout=8.0):
            log.error("Overlay: window did not initialise in time.")

    def stop(self):
        if self._root:
            self._root.after(0, self._root.destroy)

    def update(self, x: int, y: int):
        """Move the cursor indicator to screen position (x, y). Thread-safe."""
        if self._root:
            self._root.after(0, self._redraw, x, y)

    def flash(self, color: str, duration_ms: int = _FLASH_MS):
        """Briefly change cursor color for gesture feedback. Thread-safe."""
        if self._root:
            self._root.after(0, self._do_flash, color, duration_ms)

    def show(self):
        if self._root:
            self._root.after(0, self._root.deiconify)

    def hide(self):
        if self._root:
            self._root.after(0, self._root.withdraw)

    # ── Tkinter thread ──────────────────────────────────────────────────

    def _tk_main(self):
        root = tk.Tk()
        self._root = root

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", _TRANSPARENT)
        root.configure(bg=_TRANSPARENT)
        root.geometry(f"{sw}x{sh}+0+0")

        canvas = tk.Canvas(
            root, width=sw, height=sh,
            bg=_TRANSPARENT, highlightthickness=0,
        )
        canvas.pack()
        self._canvas = canvas

        # Build glow rings (outermost → innermost, decreasing opacity via color)
        glow_colors = ["#555555", "#999999", "#cccccc"]  # dark → light
        for gc in glow_colors:
            self._glow_ids.append(
                canvas.create_oval(0, 0, 1, 1, outline=gc, width=1, fill="")
            )

        # Outer ring (full brightness)
        self._glow_ids.append(
            canvas.create_oval(0, 0, 1, 1, outline="white", width=_RING_W, fill="")
        )

        # Solid dot
        self._dot_id = canvas.create_oval(0, 0, 1, 1, outline="", fill="white")

        # Crosshair lines
        self._ch_h_id = canvas.create_line(0, 0, 1, 0, fill="white", width=_LINE_W)
        self._ch_v_id = canvas.create_line(0, 0, 0, 1, fill="white", width=_LINE_W)

        self._redraw(self._x, self._y)

        # Must call update_idletasks so the HWND is allocated before we
        # query winfo_id() for the click-through setup.
        root.update_idletasks()
        _make_click_through(root)

        self._ready.set()
        root.mainloop()

    def _redraw(self, x: int, y: int):
        if not self._canvas:
            return
        self._x, self._y = x, y
        c = self._canvas
        col = self._color

        # Glow rings: concentric circles shrinking by 5 px steps
        total = len(self._glow_ids)
        for i, item_id in enumerate(self._glow_ids):
            r = _RADIUS + (total - 1 - i) * 5
            c.coords(item_id, x - r, y - r, x + r, y + r)
            # Last ring uses full color; others use glow colors
            if i < total - 1:
                glow_colors = ["#555555", "#999999", "#cccccc"]
                gc = _tint(col, glow_colors[i])
                c.itemconfig(item_id, outline=gc)
            else:
                c.itemconfig(item_id, outline=col)

        # Solid dot
        r = _FILL_RADIUS
        c.coords(self._dot_id, x - r, y - r, x + r, y + r)
        c.itemconfig(self._dot_id, fill=col)

        # Crosshair (starts just outside the dot)
        gap = _FILL_RADIUS + 3
        c.coords(self._ch_h_id, x - _CROSSHAIR, y, x + _CROSSHAIR, y)
        c.coords(self._ch_v_id, x, y - _CROSSHAIR, x, y + _CROSSHAIR)
        c.itemconfig(self._ch_h_id, fill=col)
        c.itemconfig(self._ch_v_id, fill=col)

    def _do_flash(self, color: str, duration_ms: int):
        if self._flash_job is not None:
            self._root.after_cancel(self._flash_job)
        self._color = color
        self._redraw(self._x, self._y)

        def _restore():
            self._color = _COLOR_DEFAULT
            self._redraw(self._x, self._y)
            self._flash_job = None

        self._flash_job = self._root.after(duration_ms, _restore)


# ── Helpers ─────────────────────────────────────────────────────────────

def _tint(color: str, fallback: str) -> str:
    """Return color unchanged, or fallback if color is white (glow effect)."""
    return fallback if color == "white" else color


def _make_click_through(root: tk.Tk):
    """Set WS_EX_LAYERED | WS_EX_TRANSPARENT on the Tkinter HWND (Windows)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            hwnd = root.winfo_id()
        GWL_EXSTYLE      = -20
        WS_EX_LAYERED    = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        log.info("Overlay: click-through enabled (hwnd=%d).", hwnd)
    except Exception as exc:
        log.warning("Overlay: click-through failed: %s", exc)


# ── Standalone demo ──────────────────────────────────────────────────────

def _standalone_demo():
    import time
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")

    print("Overlay standalone demo — watch the cursor indicator move around.")
    print("It should be click-through (you can click through it).")
    print("Ctrl+C to quit.\n")

    overlay = Overlay()
    overlay.start()

    import math
    sw, sh = 1920, 1080  # fallback; real size set by Tkinter
    cx, cy = sw // 2, sh // 2
    radius = 300

    try:
        t = 0.0
        while True:
            x = int(cx + radius * math.cos(t))
            y = int(cy + radius * math.sin(t) * 0.5)
            overlay.update(x, y)
            t += 0.05

            # Flash green every ~2 s
            if abs(t % (2 * math.pi)) < 0.06:
                overlay.flash("green")
                print(f"  flash green at ({x}, {y})", flush=True)

            time.sleep(0.033)
    except KeyboardInterrupt:
        print("\nStopping.")
        overlay.stop()


if __name__ == "__main__":
    _standalone_demo()
