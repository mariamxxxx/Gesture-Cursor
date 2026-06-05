"""
gesture.py — Refined gesture state machine for FingerCursor.

Improvements over v1:
  • All distances normalised by hand size (wrist→middle-MCP) so thresholds
    are invariant to how close the hand is to the camera.
  • Rotation-invariant finger-extension: tip farther from wrist than MCP
    knuckle — works for left OR right hand at any tilt angle.
  • Hysteresis on every state transition: separate enter/exit thresholds
    eliminate boundary jitter.
  • Frame-count debouncing: N consecutive frames required before any state
    change fires — one noisy frame cannot trigger a gesture.
  • Right-click = thumb + pinky (landmark 20), index must NOT be pinching.
  • Scroll = strict peace sign (index+middle extended, ring+pinky folded)
    with a 4-frame running-average delta to smooth the scroll amount.
  • Works correctly for left OR right hand.
"""

import time
import math
import logging
from collections import deque
from dataclasses import dataclass

from tracker import (
    LM_THUMB_TIP, LM_INDEX_TIP, LM_MIDDLE_TIP, LM_PINKY_TIP,
)

log = logging.getLogger(__name__)

# ── Landmark indices (not exported by tracker) ───────────────────────────
_LM_WRIST      = 0
_LM_INDEX_MCP  = 5
_LM_MIDDLE_MCP = 9
_LM_RING_TIP   = 16
_LM_RING_MCP   = 13
_LM_PINKY_MCP  = 17


# ── Gesture event types ───────────────────────────────────────────────────

@dataclass
class LeftClick:
    pass

@dataclass
class RightClick:
    pass

@dataclass
class DoubleClick:
    pass

@dataclass
class DragStart:
    pass

@dataclass
class DragEnd:
    pass

@dataclass
class Scroll:
    delta: int   # positive = scroll up, negative = down

GestureEvent = LeftClick | RightClick | DoubleClick | DragStart | DragEnd | Scroll


# ── Low-level helpers ─────────────────────────────────────────────────────

def _dist(a: dict, b: dict) -> float:
    return math.hypot(a["px"] - b["px"], a["py"] - b["py"])

def _hand_size(hand: list) -> float:
    """Pixel distance from wrist to middle-MCP — stable reference length."""
    return max(_dist(hand[_LM_WRIST], hand[_LM_MIDDLE_MCP]), 1.0)

def _is_extended(hand: list, tip_id: int, mcp_id: int) -> bool:
    """
    Rotation-invariant extension test: finger is extended when its tip is
    farther from the wrist than the MCP knuckle.  Works for left or right
    hand at any tilt or rotation.
    """
    wrist = hand[_LM_WRIST]
    return _dist(wrist, hand[tip_id]) > _dist(wrist, hand[mcp_id])


# ── Main detector ─────────────────────────────────────────────────────────

class GestureDetector:
    """
    Call update() once per tracker frame.
    Returns a (possibly empty) list of GestureEvent instances.

    All class-level constants can be hot-patched by the Settings panel.
    """

    # ── Pinch (left-click / drag) ──────────────────────────────
    PINCH_ENTER_R    = 0.30   # enter pinch  when dist/hand_size < this
    PINCH_EXIT_R     = 0.52   # exit  pinch  when dist/hand_size > this
    PINCH_ENTER_N    = 2      # consecutive frames to confirm pinch start
    PINCH_EXIT_N     = 3      # consecutive frames to confirm pinch end

    # ── Right-click (thumb + pinky) ───────────────────────────
    RCLICK_ENTER_R   = 0.38   # thumb+pinky normalised enter threshold
    RCLICK_EXIT_R    = 0.58   # thumb+pinky normalised exit  threshold
    RCLICK_ENTER_N   = 3      # frames to confirm right-click pinch
    RCLICK_EXIT_N    = 3

    # ── Drag ──────────────────────────────────────────────────
    DRAG_ENTER_R     = 0.45   # movement / hand_size to start drag (~47 px)
    DRAG_HOLD_MS     = 300    # min hold time before drag can activate

    # ── Double-click ──────────────────────────────────────────
    DOUBLE_CLICK_MS  = 400

    # ── Scroll (peace sign: index+middle up, ring+pinky down) ─
    SCROLL_ENTER_N   = 3      # frames to enter scroll mode
    SCROLL_EXIT_N    = 2      # frames to exit  scroll mode
    SCROLL_MIN_R     = 0.006  # min normalised Δy/frame avg to emit (~3 px)
    SCROLL_SCALE_R   = 0.009  # normalised movement per 1 scroll unit
    SCROLL_BUF_N     = 3      # frames to average scroll delta over

    # ──────────────────────────────────────────────────────────
    def __init__(self):
        # ── left-click pinch ──
        self._pinching        = False
        self._pinch_enter_n   = 0
        self._pinch_exit_n    = 0
        self._pinch_start_t   = 0.0
        self._pinch_start_pos = (0, 0)

        # ── drag ──
        self._drag_active     = False

        # ── double-click ──
        self._last_tap_end_t  = -999.0

        # ── right-click ──
        self._rclicking       = False
        self._rclick_enter_n  = 0
        self._rclick_exit_n   = 0

        # ── scroll ──
        self._scroll_active   = False
        self._scroll_enter_n  = 0
        self._scroll_exit_n   = 0
        self._scroll_prev_y   = 0.0          # normalised y of midpoint
        self._scroll_buf: deque[float] = deque(maxlen=self.SCROLL_BUF_N)

    # ── Public API ────────────────────────────────────────────────────────

    def update(self, landmarks: list, frame_w: int, frame_h: int) -> list[GestureEvent]:
        """
        landmarks: list of per-hand landmark lists (21 dicts each).
                   Works for left or right hand; uses landmarks[0].
        """
        if not landmarks:
            return self._on_hand_lost()

        hand  = landmarks[0]
        now   = time.monotonic()
        hs    = _hand_size(hand)
        events: list[GestureEvent] = []

        thumb  = hand[LM_THUMB_TIP]
        index  = hand[LM_INDEX_TIP]
        middle = hand[LM_MIDDLE_TIP]
        pinky  = hand[LM_PINKY_TIP]

        # Normalised distances
        d_idx  = _dist(index, thumb)  / hs
        d_pky  = _dist(pinky, thumb)  / hs

        # ── Finger-extension flags (rotation-invariant) ──────────────
        idx_ext  = _is_extended(hand, LM_INDEX_TIP,  _LM_INDEX_MCP)
        mid_ext  = _is_extended(hand, LM_MIDDLE_TIP, _LM_MIDDLE_MCP)
        ring_ext = _is_extended(hand, _LM_RING_TIP,  _LM_RING_MCP)
        pky_ext  = _is_extended(hand, LM_PINKY_TIP,  _LM_PINKY_MCP)

        # ── 1. Right-click FIRST (thumb + pinky, index open) ──────────
        # Checked before scroll so a pinky-to-thumb pinch doesn't look
        # like a peace sign (both have ring+pinky "not extended").
        rclick_raw = (d_pky < self.RCLICK_ENTER_R and d_idx > self.PINCH_EXIT_R)

        if not self._rclicking:
            if rclick_raw:
                self._rclick_enter_n = min(self._rclick_enter_n + 1, self.RCLICK_ENTER_N)
                self._rclick_exit_n  = 0
            else:
                self._rclick_enter_n = 0

            if self._rclick_enter_n >= self.RCLICK_ENTER_N:
                self._rclicking = True
                log.debug("Gesture: rclick_pinch_start")

        else:  # currently right-pinching
            rclick_open = d_pky > self.RCLICK_EXIT_R
            if rclick_open:
                self._rclick_exit_n = min(self._rclick_exit_n + 1, self.RCLICK_EXIT_N)
            else:
                self._rclick_exit_n = 0

            if self._rclick_exit_n >= self.RCLICK_EXIT_N:
                self._rclicking      = False
                self._rclick_enter_n = 0
                self._rclick_exit_n  = 0
                events.append(RightClick())
                log.debug("Gesture: right_click fired")

        # While right-click pinch is held, skip scroll and left-click
        if self._rclicking:
            return events

        # ── 2. Scroll (peace sign: index+middle up, ring+pinky folded) ─
        # Also require pinky is NOT close to thumb (d_pky > RCLICK_EXIT_R)
        # to prevent overlap with the right-click gesture.
        peace_pose = (idx_ext and mid_ext
                      and not ring_ext and not pky_ext
                      and d_pky > self.RCLICK_EXIT_R
                      and not self._pinching)

        if peace_pose:
            self._scroll_exit_n  = 0
            self._scroll_enter_n = min(self._scroll_enter_n + 1, self.SCROLL_ENTER_N)
        else:
            self._scroll_enter_n = 0
            if self._scroll_active:
                self._scroll_exit_n = min(self._scroll_exit_n + 1, self.SCROLL_EXIT_N)

        entering_scroll = (not self._scroll_active
                           and self._scroll_enter_n >= self.SCROLL_ENTER_N)
        exiting_scroll  = (self._scroll_active
                           and self._scroll_exit_n  >= self.SCROLL_EXIT_N)

        if entering_scroll:
            self._scroll_active = True
            self._scroll_prev_y = (index["y"] + middle["y"]) / 2
            self._scroll_buf.clear()
            log.debug("Gesture: scroll_enter")

        if exiting_scroll:
            self._scroll_active = False
            self._scroll_buf.clear()
            self._scroll_exit_n = 0
            log.debug("Gesture: scroll_exit")

        if self._scroll_active and peace_pose:
            mid_norm_y = (index["y"] + middle["y"]) / 2
            raw_dy = mid_norm_y - self._scroll_prev_y   # positive = hand down
            self._scroll_prev_y = mid_norm_y
            self._scroll_buf.append(raw_dy)

            if len(self._scroll_buf) == self.SCROLL_BUF_N:
                avg_dy = sum(self._scroll_buf) / self.SCROLL_BUF_N
                if abs(avg_dy) >= self.SCROLL_MIN_R:
                    # Use floor-magnitude + sign so we always emit ≥ ±1.
                    # avg_dy > 0 = hand moving down = scroll down (negative delta).
                    magnitude = max(1, int(abs(avg_dy) / self.SCROLL_SCALE_R))
                    delta = magnitude if avg_dy < 0 else -magnitude
                    events.append(Scroll(delta=delta))

            return events   # mid-scroll: skip left-click pinch

        # ── 3. Left-click pinch (index + thumb) with hysteresis ───────
        if not self._pinching:
            if d_idx < self.PINCH_ENTER_R:
                self._pinch_enter_n = min(self._pinch_enter_n + 1, self.PINCH_ENTER_N)
                self._pinch_exit_n  = 0
            else:
                self._pinch_enter_n = 0

            if self._pinch_enter_n >= self.PINCH_ENTER_N:
                self._pinching        = True
                self._pinch_enter_n   = 0
                self._pinch_exit_n    = 0
                self._pinch_start_t   = now
                self._pinch_start_pos = (index["px"], index["py"])
                log.debug("Gesture: pinch_start (d_norm=%.3f)", d_idx)

        else:  # currently left-pinching
            # ── Drag check (while held) ──
            if not self._drag_active:
                px0, py0  = self._pinch_start_pos
                moved     = _dist(index, {"px": px0, "py": py0})
                held_ms   = (now - self._pinch_start_t) * 1000
                if moved / hs > self.DRAG_ENTER_R and held_ms > self.DRAG_HOLD_MS:
                    self._drag_active = True
                    events.append(DragStart())
                    log.debug("Gesture: drag_start")

            # ── Pinch exit with debounce ──
            if d_idx > self.PINCH_EXIT_R:
                self._pinch_exit_n = min(self._pinch_exit_n + 1, self.PINCH_EXIT_N)
            else:
                self._pinch_exit_n = 0

            if self._pinch_exit_n >= self.PINCH_EXIT_N:
                self._pinching      = False
                self._pinch_enter_n = 0
                self._pinch_exit_n  = 0

                if self._drag_active:
                    self._drag_active = False
                    events.append(DragEnd())
                    log.debug("Gesture: drag_end")
                else:
                    # Classify as double-click or left-click
                    if (now - self._last_tap_end_t) * 1000 < self.DOUBLE_CLICK_MS:
                        events.append(DoubleClick())
                        log.debug("Gesture: double_click")
                    else:
                        events.append(LeftClick())
                        log.debug("Gesture: left_click")
                    self._last_tap_end_t = now

        return events

    # ── Internal ──────────────────────────────────────────────────────────

    def _on_hand_lost(self) -> list[GestureEvent]:
        events: list[GestureEvent] = []
        if self._drag_active:
            events.append(DragEnd())
            self._drag_active = False
        # Reset all counters
        self._pinching       = False
        self._pinch_enter_n  = 0
        self._pinch_exit_n   = 0
        self._rclicking      = False
        self._rclick_enter_n = 0
        self._rclick_exit_n  = 0
        self._scroll_active  = False
        self._scroll_enter_n = 0
        self._scroll_exit_n  = 0
        self._scroll_buf.clear()
        return events
