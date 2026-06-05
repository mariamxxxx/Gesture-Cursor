"""
settings.py — Settings panel UI + config persistence (JSON).

Opens as a Toplevel window on the overlay's existing Tkinter thread so
we never create a second Tk() root.  All settings are persisted to
config.json next to this file.

Usage (from FingerCursorApp):
    panel = SettingsPanel(overlay, config, on_apply=app.apply_settings)
    panel.open()   # thread-safe; posts to Tkinter event loop
"""

import json
import os
import tkinter as tk
from tkinter import ttk
import logging

log = logging.getLogger(__name__)

# ── Config file ──────────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG: dict = {
    "cam_index":     0,
    "show_debug":    False,
    "margin":        0.08,
    "smooth_alpha":  0.4,
    "max_speed":     80,
    "pinch_enter_r": 0.30,
    "drag_enter_r":  0.45,
    "drag_hold_ms":  300,
    "scroll_min_r":  0.006,
}


def load_config() -> dict:
    """Return saved config merged over defaults (new keys get defaults)."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            return {**DEFAULT_CONFIG, **saved}
        except Exception as exc:
            log.warning("Config load failed (%s) — using defaults.", exc)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        log.info("Config saved to %s", CONFIG_PATH)
    except Exception as exc:
        log.error("Config save failed: %s", exc)


# ── Settings panel ───────────────────────────────────────────────────────

class SettingsPanel:
    """
    Tkinter Toplevel settings window.

    Parameters
    ----------
    get_root : callable → tk.Tk | None
        Returns the overlay's Tk root (used to post work onto its thread).
    config : dict
        Live config dict (read on open; written on apply).
    on_apply : callable(dict)
        Called with the new config when the user clicks Apply.
        Runs on the Tkinter thread — keep it fast (no blocking I/O).
    """

    def __init__(self, get_root, config: dict, on_apply):
        self._get_root = get_root
        self._config   = config
        self._on_apply = on_apply
        self._win: tk.Toplevel | None = None

    # ── Public API (thread-safe) ─────────────────────────────────────

    def open(self):
        root = self._get_root()
        if root:
            root.after(0, self._create_or_focus)

    # ── Tkinter thread ───────────────────────────────────────────────

    def _create_or_focus(self):
        if self._win and self._win.winfo_exists():
            self._win.lift()
            self._win.focus_force()
            return
        self._build()

    def _build(self):
        root = self._get_root()
        if not root:
            return

        win = tk.Toplevel(root)
        self._win = win
        win.title("FingerCursor — Settings")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg="#1e1e2e", padx=14, pady=14)

        style = ttk.Style(win)
        style.theme_use("clam")
        style.configure("TLabelframe",
                        background="#1e1e2e", foreground="#cdd6f4",
                        bordercolor="#45475a")
        style.configure("TLabelframe.Label",
                        background="#1e1e2e", foreground="#cdd6f4",
                        font=("Segoe UI", 9, "bold"))
        style.configure("TScale", background="#1e1e2e",
                        troughcolor="#313244", sliderlength=18)
        style.configure("TCheckbutton",
                        background="#1e1e2e", foreground="#cdd6f4",
                        font=("Segoe UI", 9))
        style.configure("TCombobox", fieldbackground="#313244",
                        background="#313244", foreground="#cdd6f4")

        cfg = self._config.copy()   # working copy shown in UI

        # ── Tkinter variables ──────────────────────────────────────
        v_cam     = tk.IntVar(value=cfg["cam_index"])
        v_debug   = tk.BooleanVar(value=cfg["show_debug"])
        v_margin  = tk.DoubleVar(value=cfg["margin"])
        v_alpha   = tk.DoubleVar(value=cfg["smooth_alpha"])
        v_speed   = tk.IntVar(value=cfg["max_speed"])
        v_pinch   = tk.DoubleVar(value=cfg["pinch_enter_r"])
        v_drag_r  = tk.DoubleVar(value=cfg["drag_enter_r"])
        v_drag_ms = tk.IntVar(value=cfg["drag_hold_ms"])
        v_scroll  = tk.DoubleVar(value=cfg["scroll_min_r"])

        def section(text):
            f = ttk.LabelFrame(win, text=text, padding=(10, 6, 10, 8))
            f.pack(fill="x", pady=(0, 8))
            return f

        def slider_row(parent, label, var, lo, hi, fmt="{:.2f}", row=0):
            tk.Label(parent, text=label, bg="#1e1e2e", fg="#cdd6f4",
                     font=("Segoe UI", 9), anchor="w", width=20
                     ).grid(row=row, column=0, sticky="w", padx=(0, 6))

            val_lbl = tk.Label(parent, text=fmt.format(var.get()),
                               bg="#1e1e2e", fg="#a6e3a1",
                               font=("Segoe UI Mono", 9), width=6)
            val_lbl.grid(row=row, column=2, sticky="e")

            def _update(v):
                val_lbl.config(text=fmt.format(float(v)))

            sc = ttk.Scale(parent, from_=lo, to=hi, variable=var,
                           orient="horizontal", length=180, command=_update)
            sc.grid(row=row, column=1, sticky="ew", padx=4)
            parent.columnconfigure(1, weight=1)

        # ── Camera ────────────────────────────────────────────────
        cam_f = section("Camera")
        tk.Label(cam_f, text="Webcam index", bg="#1e1e2e", fg="#cdd6f4",
                 font=("Segoe UI", 9), width=20, anchor="w"
                 ).grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(cam_f, textvariable=v_cam, width=5, state="readonly",
                          values=["0", "1", "2", "3"])
        cb.grid(row=0, column=1, sticky="w", padx=4, pady=2)

        ttk.Checkbutton(cam_f, text="Show debug landmark preview",
                        variable=v_debug
                        ).grid(row=1, column=0, columnspan=3,
                               sticky="w", pady=(4, 0))

        # ── Cursor ────────────────────────────────────────────────
        cur_f = section("Cursor")
        slider_row(cur_f, "Smoothing  (alpha)",  v_alpha,  0.10, 1.00, "{:.2f}", 0)
        slider_row(cur_f, "Speed cap  (px/frame)", v_speed, 20,  200, "{:.0f}", 1)
        slider_row(cur_f, "Dead-zone  (margin)",  v_margin, 0.02, 0.30, "{:.2f}", 2)

        # ── Gestures ──────────────────────────────────────────────
        ges_f = section("Gestures")
        slider_row(ges_f, "Pinch sensitivity",     v_pinch,   0.15, 0.50, "{:.2f}", 0)
        slider_row(ges_f, "Drag movement thresh",  v_drag_r,  0.20, 0.70, "{:.2f}", 1)
        slider_row(ges_f, "Drag hold (ms)",        v_drag_ms, 100,  600,  "{:.0f}", 2)
        slider_row(ges_f, "Scroll sensitivity",    v_scroll,  0.002, 0.030, "{:.3f}", 3)

        # ── Buttons ───────────────────────────────────────────────
        btn_f = tk.Frame(win, bg="#1e1e2e")
        btn_f.pack(fill="x", pady=(4, 0))

        def _apply():
            new_cfg = {
                "cam_index":     int(v_cam.get()),
                "show_debug":    bool(v_debug.get()),
                "margin":        round(v_margin.get(), 3),
                "smooth_alpha":  round(v_alpha.get(), 2),
                "max_speed":     int(v_speed.get()),
                "pinch_enter_r": round(v_pinch.get(), 3),
                "drag_enter_r":  round(v_drag_r.get(), 3),
                "drag_hold_ms":  int(v_drag_ms.get()),
                "scroll_min_r":  round(v_scroll.get(), 4),
            }
            save_config(new_cfg)
            self._config.update(new_cfg)
            self._on_apply(new_cfg)
            win.destroy()

        tk.Button(btn_f, text="Apply & Restart", command=_apply,
                  bg="#a6e3a1", fg="#1e1e2e", activebackground="#94e2d5",
                  font=("Segoe UI", 9, "bold"), relief="flat",
                  padx=12, pady=5
                  ).pack(side="left", padx=(0, 8))

        tk.Button(btn_f, text="Cancel", command=win.destroy,
                  bg="#45475a", fg="#cdd6f4", activebackground="#585b70",
                  font=("Segoe UI", 9), relief="flat",
                  padx=12, pady=5
                  ).pack(side="left")

        # Centre window on screen
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        ww = win.winfo_reqwidth()
        wh = win.winfo_reqheight()
        win.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")
