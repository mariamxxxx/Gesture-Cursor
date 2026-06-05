"""
cursor_mover.py — OS cursor movement abstraction (Windows + macOS).

Wraps PyAutoGUI (and ctypes on Windows) for moving/clicking the real
system cursor. Includes speed-capping to prevent jumpy movement between
frames.

Run directly for a standalone demo that moves the cursor to each screen
corner and back to centre.
"""

import sys
import time
import logging
import pyautogui

log = logging.getLogger(__name__)

# Disable PyAutoGUI's fail-safe corner — the finger tracker legitimately
# moves the cursor to screen corners, so we don't want it to abort.
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0  # remove the default 0.1 s inter-call pause

# Try importing Windows-specific helpers (graceful no-op on macOS/Linux)
_win32 = False
try:
    import ctypes
    _win32 = sys.platform == "win32"
except ImportError:
    pass


class CursorMover:
    """
    Moves the OS cursor and fires mouse events.

    Parameters
    ----------
    max_speed : int
        Maximum pixels the cursor may jump in a single frame.
        Clamps large deltas to prevent wild jumps when tracking is lost.
    """

    def __init__(self, max_speed: int = 80):
        self.max_speed = max_speed
        self._last_x: float | None = None
        self._last_y: float | None = None

    # ------------------------------------------------------------------
    # Core movement
    # ------------------------------------------------------------------

    def move_to(self, x: float, y: float) -> tuple[int, int]:
        """
        Move the OS cursor to (x, y), clamped by max_speed.

        Returns the actual (x, y) the cursor was moved to.
        """
        x, y = int(round(x)), int(round(y))

        if self._last_x is not None:
            dx = x - self._last_x
            dy = y - self._last_y
            dist = (dx ** 2 + dy ** 2) ** 0.5
            if dist > self.max_speed:
                scale = self.max_speed / dist
                x = int(round(self._last_x + dx * scale))
                y = int(round(self._last_y + dy * scale))

        self._last_x, self._last_y = x, y

        if _win32:
            _win32_move(x, y)
        else:
            pyautogui.moveTo(x, y)

        return x, y

    def reset_speed_cap(self):
        """Call this after a tracking gap so the cap doesn't mis-clamp."""
        self._last_x = self._last_y = None

    # ------------------------------------------------------------------
    # Click / scroll helpers (thin wrappers around PyAutoGUI)
    # ------------------------------------------------------------------

    def click(self):
        pyautogui.click()

    def right_click(self):
        pyautogui.rightClick()

    def double_click(self):
        pyautogui.doubleClick()

    def mouse_down(self):
        pyautogui.mouseDown()

    def mouse_up(self):
        pyautogui.mouseUp()

    def scroll(self, delta: int):
        """Positive delta scrolls up, negative scrolls down."""
        pyautogui.scroll(delta)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def screen_size() -> tuple[int, int]:
        return pyautogui.size()


# ------------------------------------------------------------------
# Windows low-level cursor move via SendInput (faster than PyAutoGUI)
# ------------------------------------------------------------------

def _win32_move(x: int, y: int):
    """Use ctypes SendInput for lower-latency cursor movement on Windows."""
    MOUSEEVENTF_MOVE      = 0x0001
    MOUSEEVENTF_ABSOLUTE  = 0x8000

    # SendInput wants coordinates in the range [0, 65535] normalised to screen
    sw, sh = pyautogui.size()
    nx = int(x * 65535 / (sw - 1))
    ny = int(y * 65535 / (sh - 1))

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx",          ctypes.c_long),
            ("dy",          ctypes.c_long),
            ("mouseData",   ctypes.c_ulong),
            ("dwFlags",     ctypes.c_ulong),
            ("time",        ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        class _INPUT(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT)]
        _anonymous_ = ("_input",)
        _fields_    = [("type", ctypes.c_ulong), ("_input", _INPUT)]

    inp = INPUT(
        type=0,
        mi=MOUSEINPUT(
            dx=nx, dy=ny,
            mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
            time=0,
            dwExtraInfo=None,
        ),
    )
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# ----------------------------------------------------------------------
# Standalone demo
# ----------------------------------------------------------------------

def _standalone_demo():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")

    mover = CursorMover(max_speed=9999)   # no speed cap for demo
    w, h  = mover.screen_size()

    print("=" * 55)
    print("FingerCursor — cursor_mover.py standalone demo")
    print(f"Screen: {w} × {h}")
    print("Moving cursor to each corner, then centre …")
    print("=" * 55)

    corners = [
        ("top-left",     50,      50),
        ("top-right",    w - 50,  50),
        ("bottom-right", w - 50,  h - 50),
        ("bottom-left",  50,      h - 50),
        ("centre",       w // 2,  h // 2),
    ]

    for label, x, y in corners:
        print(f"  → {label:15s}  ({x:>5}, {y:>5})", flush=True)
        mover.reset_speed_cap()
        mover.move_to(x, y)
        time.sleep(0.8)

    print("\nSpeed-cap test — large jump clamped to max_speed=80 px:")
    mover2 = CursorMover(max_speed=80)
    mover2.move_to(w // 2, h // 2)   # anchor
    time.sleep(0.3)
    actual = mover2.move_to(0, 0)    # huge jump — should be clamped
    print(f"  Requested (0, 0), landed at {actual}  (delta capped to 80 px)")
    time.sleep(0.5)

    print("\nDone.")


if __name__ == "__main__":
    _standalone_demo()
