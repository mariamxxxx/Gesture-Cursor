"""
tracker.py — MediaPipe hand tracking loop (background thread).

Captures webcam frames, runs MediaPipe Hands, and yields landmark data
via a thread-safe queue. In standalone mode (run directly) it prints
index-finger-tip coordinates and shows a debug window.
"""

import cv2
import mediapipe as mp
import queue
import threading
import time
import logging

log = logging.getLogger(__name__)

# MediaPipe landmark index constants
LM_THUMB_TIP   = 4
LM_INDEX_TIP   = 8
LM_MIDDLE_TIP  = 12
LM_RING_TIP    = 16
LM_PINKY_TIP   = 20

FRAME_W = 640
FRAME_H = 480
TARGET_FPS = 30


class HandTracker:
    """
    Runs MediaPipe Hands in a background thread.

    Detected landmark data is placed on `self.queue` as dicts:
        {
            "landmarks": list of {id, x, y, z} for each hand,
            "frame_w": int,
            "frame_h": int,
            "debug_frame": np.ndarray | None   # only when show_debug=True
        }

    Usage:
        tracker = HandTracker(cam_index=0, show_debug=False)
        tracker.start()
        ...
        data = tracker.queue.get_nowait()
        tracker.stop()
    """

    def __init__(self, cam_index: int = 0, show_debug: bool = False,
                 max_hands: int = 2):
        self.cam_index  = cam_index
        self.show_debug = show_debug
        self.max_hands  = max_hands

        # Callers pull from this queue; tracker drops frames when full.
        self.queue: queue.Queue = queue.Queue(maxsize=2)

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="HandTracker")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._thread = None

    # ------------------------------------------------------------------
    # Internal loop (runs in background thread)
    # ------------------------------------------------------------------

    def _run(self):
        mp_hands = mp.solutions.hands
        mp_draw  = mp.solutions.drawing_utils
        mp_styles = mp.solutions.drawing_styles

        cap = self._open_camera()
        if cap is None:
            log.error("Tracker: no camera available — exiting thread.")
            return

        frame_interval = 1.0 / TARGET_FPS
        consecutive_fails = 0
        MAX_CONSECUTIVE_FAILS = 30  # ~1 s at 30 fps before attempting reconnect

        with mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=self.max_hands,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        ) as hands:
            log.info("Tracker: MediaPipe Hands initialised.")
            while not self._stop_event.is_set():
                t0 = time.monotonic()

                ret, frame = cap.read()
                if not ret:
                    consecutive_fails += 1
                    if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                        log.warning("Tracker: %d consecutive read failures — reconnecting camera.", consecutive_fails)
                        cap.release()
                        time.sleep(1.0)
                        cap = self._open_camera()
                        if cap is None:
                            log.error("Tracker: reconnect failed, exiting thread.")
                            return
                        consecutive_fails = 0
                    else:
                        time.sleep(0.05)
                    continue
                consecutive_fails = 0

                # Mirror + resize to standard processing resolution
                frame = cv2.flip(frame, 1)
                frame = cv2.resize(frame, (FRAME_W, FRAME_H))

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb.flags.writeable = False
                results = hands.process(rgb)
                rgb.flags.writeable = True

                all_hands = []
                debug_frame = None

                if results.multi_hand_landmarks:
                    for hand_lms in results.multi_hand_landmarks:
                        lm_list = []
                        for idx, lm in enumerate(hand_lms.landmark):
                            lm_list.append({
                                "id": idx,
                                "x":  lm.x,   # normalised [0,1]
                                "y":  lm.y,   # normalised [0,1]
                                "z":  lm.z,
                                # pixel coords in processing-resolution frame
                                "px": int(lm.x * FRAME_W),
                                "py": int(lm.y * FRAME_H),
                            })
                        all_hands.append(lm_list)

                    if self.show_debug:
                        debug_frame = frame.copy()
                        for hand_lms in results.multi_hand_landmarks:
                            mp_draw.draw_landmarks(
                                debug_frame,
                                hand_lms,
                                mp_hands.HAND_CONNECTIONS,
                                mp_styles.get_default_hand_landmarks_style(),
                                mp_styles.get_default_hand_connections_style(),
                            )

                payload = {
                    "landmarks":   all_hands,
                    "frame_w":     FRAME_W,
                    "frame_h":     FRAME_H,
                    "debug_frame": debug_frame,
                }

                # Drop frame rather than block if consumer is slow
                try:
                    self.queue.put_nowait(payload)
                except queue.Full:
                    pass

                # Throttle to TARGET_FPS
                elapsed = time.monotonic() - t0
                sleep_for = frame_interval - elapsed
                if sleep_for > 0:
                    time.sleep(sleep_for)

        cap.release()
        log.info("Tracker: camera released, thread exiting.")

    def _open_camera(self):
        cap = cv2.VideoCapture(self.cam_index)
        if not cap.isOpened():
            log.error("Tracker: could not open camera index %d.", self.cam_index)
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
        cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
        log.info("Tracker: camera %d opened at %dx%d.",
                 self.cam_index, FRAME_W, FRAME_H)
        return cap


# ----------------------------------------------------------------------
# Standalone demo — run this file directly to test
# ----------------------------------------------------------------------

def _standalone_demo():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
    )

    print("=" * 60)
    print("FingerCursor — tracker.py standalone demo")
    print("Point your index finger at the camera.")
    print("Press Q in the preview window (or Ctrl+C here) to quit.")
    print("=" * 60)

    tracker = HandTracker(cam_index=0, show_debug=True)
    tracker.start()

    cv2.namedWindow("FingerCursor — debug", cv2.WINDOW_NORMAL)

    last_print = 0.0   # throttle console output to ~10 lines/sec

    try:
        while True:
            try:
                data = tracker.queue.get(timeout=0.1)
            except queue.Empty:
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                continue

            hands = data["landmarks"]
            now = time.monotonic()

            if now - last_print >= 0.1:   # print at most 10×/sec
                last_print = now
                if hands:
                    index_tip = hands[0][LM_INDEX_TIP]
                    thumb_tip = hands[0][LM_THUMB_TIP]
                    print(
                        f"index tip  norm=({index_tip['x']:.3f}, {index_tip['y']:.3f})"
                        f"  px=({index_tip['px']:>4}, {index_tip['py']:>4})"
                        f"   thumb norm=({thumb_tip['x']:.3f}, {thumb_tip['y']:.3f})",
                        flush=True,
                    )
                else:
                    print("(no hand detected)", flush=True)

            if data["debug_frame"] is not None:
                cv2.imshow("FingerCursor — debug", data["debug_frame"])

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        tracker.stop()
        cv2.destroyAllWindows()
        print("\nTracker stopped.")


if __name__ == "__main__":
    _standalone_demo()
