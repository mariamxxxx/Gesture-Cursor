r# FingerCursor

Control your mouse with your index finger using the laptop webcam. No extra hardware needed.

---

## Requirements

- Windows 10/11
- Python 3.11
- A webcam

Install dependencies once:

```
python3.11 -m pip install -r requirements.txt
```

---

## Starting the app

```
python3.11 finger_cursor/main.py
```

A green icon appears in the system tray (bottom-right of the taskbar). The app starts tracking immediately.

---

## Hand position

- Sit 40–70 cm from the webcam.
- Keep your hand in front of the camera, centered. The app works for both left and right hands.
- Point your **index finger** to move the cursor. The cursor follows your fingertip.
- You do not need to keep your hand perfectly still — small tremor is filtered automatically.

---

## Gestures

All gestures are performed with a **single hand**.

| Gesture          | How to do it                                                 | What happens                       |
| ---------------- | ------------------------------------------------------------ | ---------------------------------- |
| **Move cursor**  | Raise index finger, move hand                                | Cursor follows index fingertip     |
| **Left click**   | Pinch index finger to thumb, then release                    | Click fires on release             |
| **Double click** | Pinch and release twice within 400 ms                        | Double-click                       |
| **Right click**  | Touch pinky finger to thumb (index stays open)               | Right-click fires on release       |
| **Drag**         | Pinch, hold 300 ms, then move hand                           | Drag starts; release pinch to drop |
| **Scroll**       | Raise index + middle fingers (peace sign), move hand up/down | Scrolls in that direction          |

**Tips for reliable gestures:**

- **Pinch** (left click): bring the tip of your index finger to your thumb tip. You should see the flash turn green when the click fires.
- **Right click**: curl your pinky toward your thumb while keeping your index finger extended. The yellow flash confirms it.
- **Scroll**: make a clear peace/scissors sign — index and middle up, ring and pinky folded down. Move your whole hand up or down to scroll. Drop one of the two raised fingers to stop.
- **Drag**: pinch and _hold_ for at least 300 ms before moving. Moving immediately after pinching will not trigger drag.

---

## Tray icon

Right-click the tray icon for the menu:

| Menu item            | Action                   |
| -------------------- | ------------------------ |
| **Disable / Enable** | Pause or resume tracking |
| **Settings**         | Open the settings panel  |
| **Quit**             | Exit the app             |

### Keyboard shortcut

**Ctrl + Shift + F** toggles tracking on/off from anywhere.

---

## Overlay indicator

A white crosshair circle follows your finger on screen. It changes color to confirm gestures:

| Color  | Gesture confirmed |
| ------ | ----------------- |
| Green  | Left click        |
| Yellow | Right click       |
| Cyan   | Double click      |
| Orange | Drag started      |
| White  | Drag released     |
| Blue   | Scroll            |

---

## Settings

Open via tray icon → **Settings**.

### Camera

| Setting                | What it does                                                 |
| ---------------------- | ------------------------------------------------------------ |
| **Webcam index**       | Which camera to use (0 = built-in, 1 = first external, etc.) |
| **Show debug preview** | Opens a second window showing the MediaPipe skeleton overlay |

### Cursor

| Setting                  | Range       | What it does                                                                                                                   |
| ------------------------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **Smoothing (alpha)**    | 0.10 – 1.00 | How closely the cursor tracks raw finger movement. Low = smooth but laggy. High = responsive but jittery. Default: 0.4         |
| **Speed cap (px/frame)** | 20 – 200    | Maximum cursor jump per frame. Lower values prevent accidental large jumps. Default: 80                                        |
| **Dead-zone (margin)**   | 0.02 – 0.30 | Fraction of the camera frame ignored at each edge. Increase if the cursor jumps when your hand is near the edge. Default: 0.08 |

### Gestures

| Setting                     | Range         | What it does                                                                                                             |
| --------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **Pinch sensitivity**       | 0.15 – 0.50   | How far index must close to thumb to register a pinch. Lower = easier to trigger but more false positives. Default: 0.30 |
| **Drag movement threshold** | 0.20 – 0.70   | How much the hand must move while pinching before drag starts. Increase if drag triggers accidentally. Default: 0.45     |
| **Drag hold (ms)**          | 100 – 600     | How long you must hold the pinch before drag activates. Default: 300 ms                                                  |
| **Scroll sensitivity**      | 0.002 – 0.030 | Minimum hand movement per frame to emit a scroll tick. Lower = scrolls from very small movements. Default: 0.006         |

Click **Apply & Restart** to save and restart tracking with the new values. Settings are saved to `finger_cursor/config.json` and persist across restarts.

---

## Troubleshooting

**Cursor doesn't move**

- Check the tray icon is green (tracking active). Press Ctrl+Shift+F to toggle.
- Make sure your hand is visible and well-lit in the camera frame.
- Try `python3.11 finger_cursor/tracker.py` to verify the camera and landmark detection are working.

**Can't reach screen corners**

- Move your hand closer to the edge of the camera frame.
- Lower the **Dead-zone (margin)** in Settings.

**Cursor is shaky**

- Lower **Smoothing (alpha)** in Settings (e.g. 0.25).
- Ensure good, even lighting with no strong backlighting behind your hand.

**Gestures fire accidentally**

- Increase **Pinch sensitivity** (higher number = harder to trigger).
- Increase **Drag hold** if drag activates when you only meant to click.
- For scroll false positives: increase **Scroll sensitivity**.

**Wrong webcam**

- Change **Webcam index** in Settings (try 1 or 2 if 0 is not your hand-facing camera).

**App crashes / camera disconnects**

- The app will automatically attempt to reconnect the camera and restart tracking after 2 seconds. If the tray icon is still present, tracking will resume on its own.

---

## Command-line options

These override saved settings for the current session only:

```
python3.11 finger_cursor/main.py [options]

  --cam N       Webcam index (default: 0)
  --debug       Show MediaPipe skeleton debug window
  --margin F    Dead-zone margin, 0–0.3 (default: 0.08)
  --alpha F     Smoothing alpha, 0.1–1.0 (default: 0.4)
  --speed N     Max cursor jump per frame in pixels (default: 80)
```
