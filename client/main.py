# =============================================================================
# Project Scolecite — AI Quant Trading Terminal
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Professional real-time trading dashboard built with customtkinter.
Connects to the FastAPI server via REST + SSE.

Design:
  - Dark navy theme (#1a1b1e / #25262b)
  - Cyan (#00d4aa) & Gold (#f0b90b) accent colours
  - LED-style status indicators
  - TabView: Overview | AI Strategy | AI Chat | Journal | Full Logs
  - Ticker cards with signal badges
  - Multi-timeframe chart selector
  - Confidence gauge & AI cost display
  - Responsive layout with min-size guard
"""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from typing import Any, Optional

import customtkinter as ctk
import httpx
import matplotlib
import pytz
import re

matplotlib.use("Agg")  # non-interactive backend
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
BG_PRIMARY = "#1a1b1e"
BG_WIDGET = "#25262b"
BG_HOVER = "#2c2e33"
BG_INPUT = "#1e1f23"
TEXT_PRIMARY = "#c1c2c5"
TEXT_SECONDARY = "#909296"
TEXT_HEADING = "#e9ecef"
ACCENT_CYAN = "#00d4aa"
ACCENT_GOLD = "#f0b90b"
ACCENT_RED = "#ff6b6b"
ACCENT_GREEN = "#51cf66"
ACCENT_AMBER = "#fcc419"
ACCENT_PURPLE = "#da77f2"
BORDER_SUBTLE = "#373a40"

FONT_HEADING = ("Pretendard", 13, "bold")
FONT_BODY = ("Pretendard", 12)
FONT_SMALL = ("Pretendard", 11)
FONT_MONO = ("JetBrains Mono", 11)
FONT_MONO_SM = ("JetBrains Mono", 10)
FONT_TITLE = ("Pretendard", 22, "bold")
FONT_SUBTITLE = ("Pretendard", 11)
FONT_BIG_NUM = ("JetBrains Mono", 20, "bold")
CORNER_R = 10
PAD = 14

# ---------------------------------------------------------------------------
# K / M / B / T numeric helpers
# ---------------------------------------------------------------------------
_SUFFIX_MAP = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_SUFFIX_RE = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*([KkMmBbTt])?\s*$"
)


def parse_human_number(text: str) -> float | None:
    """Parse '10B', '5.5M', '300K', '1.2T' or plain numbers. Returns None on failure."""
    m = _SUFFIX_RE.match(text.strip())
    if not m:
        return None
    value = float(m.group(1))
    suffix = m.group(2)
    if suffix:
        value *= _SUFFIX_MAP[suffix.upper()]
    return value


def format_human_number(value: float) -> str:
    """Format large numbers: 1_000_000_000 → '1B', 5_500_000 → '5.5M'."""
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    if abs_val >= 1e12:
        return f"{sign}{abs_val / 1e12:.4g}T"
    if abs_val >= 1e9:
        return f"{sign}{abs_val / 1e9:.4g}B"
    if abs_val >= 1e6:
        return f"{sign}{abs_val / 1e6:.4g}M"
    if abs_val >= 1e3:
        return f"{sign}{abs_val / 1e3:.4g}K"
    if abs_val == int(abs_val):
        return f"{sign}{int(abs_val)}"
    return f"{sign}{value:.4g}"

# ---------------------------------------------------------------------------
# Connection config
# ---------------------------------------------------------------------------
SERVER_URL = "http://localhost:8000"
SSE_URL = f"{SERVER_URL}/api/stream"
POLL_INTERVAL_MS = 5000

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ===========================================================================
# SSE Reader (background thread)
# ===========================================================================
class SSEReader:
    """Reads Server-Sent Events from the FastAPI server in a daemon thread."""

    def __init__(self, url: str, callback) -> None:
        self._url = url
        self._callback = callback
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        while self._running:
            try:
                with httpx.stream("GET", self._url, timeout=None) as resp:
                    event_type = ""
                    data_buf = ""
                    for line in resp.iter_lines():
                        if not self._running:
                            break
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_buf = line[5:].strip()
                        elif line == "":
                            if event_type and data_buf:
                                try:
                                    self._callback(event_type, json.loads(data_buf))
                                except json.JSONDecodeError:
                                    pass
                            event_type = ""
                            data_buf = ""
            except Exception:
                if self._running:
                    time.sleep(5)


# ===========================================================================
# Reusable UI helpers
# ===========================================================================
def _card(parent: Any, **kw: Any) -> ctk.CTkFrame:
    """Create a styled card frame."""
    return ctk.CTkFrame(
        parent,
        fg_color=BG_WIDGET,
        corner_radius=CORNER_R,
        border_width=1,
        border_color=BORDER_SUBTLE,
        **kw,
    )


def _label(parent: Any, text: str, **kw: Any) -> ctk.CTkLabel:
    defaults: dict[str, Any] = dict(text_color=TEXT_PRIMARY, font=FONT_BODY)
    defaults.update(kw)
    return ctk.CTkLabel(parent, text=text, **defaults)


def _heading(parent: Any, text: str, **kw: Any) -> ctk.CTkLabel:
    defaults: dict[str, Any] = dict(text_color=TEXT_HEADING, font=FONT_HEADING)
    defaults.update(kw)
    return ctk.CTkLabel(parent, text=text, **defaults)


# ===========================================================================
# Main Application Window
# ===========================================================================
class TradingBotApp(ctk.CTk):
    """AI Quant Trading Terminal — professional desktop dashboard."""

    def __init__(self) -> None:
        super().__init__()

        self.title("SCOLECITE  ·  AI Quant Trading Terminal")
        self.configure(fg_color=BG_PRIMARY)
        self.minsize(1100, 650)

        # Start at 82 % of screen, centred
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = int(sw * 0.82), int(sh * 0.82)
        x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._http = httpx.Client(base_url=SERVER_URL, timeout=10)
        self._sse: Optional[SSEReader] = None

        # State
        self._bot_running = False
        self._chart_data: dict[str, list[float]] = {}
        self._pulse_phase = 0
        self._selected_timeframe = "1h"
        self._selected_chart_symbol: Optional[str] = None
        self._chat_history: list[dict] = []

        # Throttle / efficient-render state
        self._last_chart_draw: float = 0.0
        self._chart_throttle_s: float = 1.5
        self._chart_redraw_pending: bool = False
        self._chart_dirty: bool = False
        self._chart_lines: dict[str, Any] = {}
        self._pending_sse_updates: list[tuple[str, dict]] = []
        self._sse_flush_scheduled: bool = False
        self._ui_throttle_ms: int = 150

        self._build_ui()
        self.after(500, self._check_sidebar_scroll)
        self._start_sse()
        self._poll_status()

    # ==================================================================
    # UI Construction
    # ==================================================================
    def _build_ui(self) -> None:
        # Top bar
        self._build_top_bar()

        # Body: sidebar + main content
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD))
        body.grid_columnconfigure(0, weight=0, minsize=280)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_main_content(body)

    # ---- TOP BAR ----
    def _build_top_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=BG_WIDGET, corner_radius=0, height=56)
        bar.pack(fill="x", padx=0, pady=0)
        bar.pack_propagate(False)

        # Left: logo + title
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.pack(side="left", padx=20)

        _label(left, text="◆", font=("Pretendard", 20, "bold"),
               text_color=ACCENT_CYAN).pack(side="left")
        _label(left, text="  SCOLECITE", font=FONT_TITLE,
               text_color=TEXT_HEADING).pack(side="left")
        _label(left, text="  AI Quant Trading Terminal", font=FONT_SUBTITLE,
               text_color=TEXT_SECONDARY).pack(side="left", padx=(6, 0))

        # Right: cost badge + clock + mode badge
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=20)

        self._mode_badge = ctk.CTkLabel(
            right, text="  PAPER  ", font=("JetBrains Mono", 11, "bold"),
            fg_color="#3b3416", text_color=ACCENT_GOLD, corner_radius=6,
        )
        self._mode_badge.pack(side="right", padx=(10, 0))

        self._cost_badge = ctk.CTkLabel(
            right, text="  $0.00  ", font=FONT_MONO_SM,
            fg_color=BG_INPUT, text_color=TEXT_SECONDARY, corner_radius=6,
        )
        self._cost_badge.pack(side="right", padx=(10, 0))

        self._clock_label = _label(right, text="--:--:--",
                                   font=FONT_MONO, text_color=TEXT_SECONDARY)
        self._clock_label.pack(side="right")
        self._tick_clock()

        # Pulse dot (visible when bot running)
        self._pulse_dot = _label(right, text="●", font=("Pretendard", 14),
                                 text_color=BG_WIDGET)
        self._pulse_dot.pack(side="right", padx=(0, 12))

        # Rest mode indicator
        self._rest_badge = ctk.CTkLabel(
            right, text="", font=FONT_MONO_SM,
            fg_color=BG_WIDGET, text_color=ACCENT_AMBER, corner_radius=6,
        )
        self._rest_badge.pack(side="right", padx=(0, 8))

    def _tick_clock(self) -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S  UTC")
        self._clock_label.configure(text=now)
        self.after(1000, self._tick_clock)

    # ---- SIDEBAR ----
    def _build_sidebar(self, parent: ctk.CTkFrame) -> None:
        sidebar = ctk.CTkFrame(parent, fg_color="transparent", width=280)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, PAD), pady=(0, 0))
        sidebar.grid_propagate(False)

        # Scrollable container (scrollbar auto-hides when content fits)
        scroll = ctk.CTkScrollableFrame(
            sidebar, fg_color="transparent", width=260,
            scrollbar_button_color=BG_PRIMARY,
            scrollbar_button_hover_color=BG_HOVER,
        )
        scroll.pack(fill="both", expand=True, pady=(32, 0))
        self._sidebar_scroll = scroll
        scroll._scrollbar.grid_remove()  # hidden by default
        scroll.bind("<Configure>", lambda e: self._check_sidebar_scroll())

        # --- Connection status card ---
        conn_card = _card(scroll)
        conn_card.pack(fill="x", pady=(0, PAD))

        _heading(conn_card, text="Connection Status").pack(
            anchor="w", padx=PAD, pady=(PAD, 8))

        self._status_indicators: dict[str, tuple[ctk.CTkLabel, ctk.CTkLabel]] = {}
        for name in ["Server", "Claude API", "Grok API", "Alpaca"]:
            row = ctk.CTkFrame(conn_card, fg_color="transparent")
            row.pack(fill="x", padx=PAD, pady=2)
            dot = _label(row, text="●", font=("Pretendard", 12),
                         text_color=TEXT_SECONDARY)
            dot.pack(side="left")
            name_lbl = _label(row, text=f"  {name}", text_color=TEXT_SECONDARY)
            name_lbl.pack(side="left")
            status_lbl = _label(row, text="checking…", font=FONT_SMALL,
                                text_color=TEXT_SECONDARY)
            status_lbl.pack(side="right")
            key = name.lower().replace(" api", "").replace(" ", "_")
            self._status_indicators[key] = (dot, status_lbl)

        ctk.CTkFrame(conn_card, fg_color="transparent", height=8).pack()

        # --- Bot Controls card ---
        ctrl_card = _card(scroll)
        ctrl_card.pack(fill="x", pady=(0, PAD))

        _heading(ctrl_card, text="Bot Controls").pack(
            anchor="w", padx=PAD, pady=(PAD, 10))

        btn_row = ctk.CTkFrame(ctrl_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=PAD)
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)

        self._start_btn = ctk.CTkButton(
            btn_row, text="▶  Start", height=36, corner_radius=8,
            fg_color="#1b4332", hover_color="#2d6a4f",
            text_color=ACCENT_GREEN, font=("Pretendard", 12, "bold"),
            command=self._on_start,
        )
        self._start_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self._stop_btn = ctk.CTkButton(
            btn_row, text="■  Stop", height=36, corner_radius=8,
            fg_color=BG_HOVER, hover_color="#495057",
            text_color=TEXT_SECONDARY, font=("Pretendard", 12, "bold"),
            command=self._on_stop, state="disabled",
        )
        self._stop_btn.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        self._kill_btn = ctk.CTkButton(
            ctrl_card, text="⚠  EMERGENCY KILL SWITCH", height=42,
            corner_radius=8, fg_color="#3b1219", hover_color="#5c1a27",
            text_color=ACCENT_RED, font=("Pretendard", 12, "bold"),
            command=self._on_emergency,
        )
        self._kill_btn.pack(fill="x", padx=PAD, pady=(8, PAD))

        # --- Portfolio card ---
        port_card = _card(scroll)
        port_card.pack(fill="x", pady=(0, PAD))

        _heading(port_card, text="Portfolio").pack(
            anchor="w", padx=PAD, pady=(PAD, 6))

        self._equity_label = _label(
            port_card, text="$0.00", font=FONT_BIG_NUM, text_color=TEXT_HEADING)
        self._equity_label.pack(anchor="w", padx=PAD, pady=(0, 2))

        self._equity_sub = _label(
            port_card, text="Total Equity", font=FONT_SMALL,
            text_color=TEXT_SECONDARY)
        self._equity_sub.pack(anchor="w", padx=PAD, pady=(0, 10))

        metrics = ctk.CTkFrame(port_card, fg_color="transparent")
        metrics.pack(fill="x", padx=PAD, pady=(0, PAD))
        metrics.grid_columnconfigure(0, weight=1)
        metrics.grid_columnconfigure(1, weight=1)

        self._portfolio_vals: dict[str, ctk.CTkLabel] = {}
        for idx, (key, label) in enumerate([
            ("cash", "Cash"), ("daily_pl", "Daily P&L"),
            ("total_pl", "Total P&L"), ("positions", "Positions"),
        ]):
            r, c = divmod(idx, 2)
            cell = ctk.CTkFrame(metrics, fg_color=BG_INPUT, corner_radius=6)
            cell.grid(row=r, column=c, padx=3, pady=3, sticky="ew")
            _label(cell, text=label, font=FONT_SMALL,
                   text_color=TEXT_SECONDARY).pack(anchor="w", padx=8, pady=(6, 0))
            v = _label(cell, text="—", font=FONT_MONO, text_color=TEXT_PRIMARY)
            v.pack(anchor="w", padx=8, pady=(0, 6))
            self._portfolio_vals[key] = v

        # --- Ticker Cards ---
        ticker_card = _card(scroll)
        ticker_card.pack(fill="x", pady=(0, PAD))
        _heading(ticker_card, text="Ticker Cards").pack(
            anchor="w", padx=PAD, pady=(PAD, 6))
        self._ticker_cards_frame = ctk.CTkFrame(ticker_card, fg_color="transparent")
        self._ticker_cards_frame.pack(fill="x", padx=PAD, pady=(0, PAD))
        self._ticker_card_labels: dict[str, dict[str, ctk.CTkLabel]] = {}

        # --- Tracked Symbols card ---
        sym_card = _card(scroll)
        sym_card.pack(fill="x", pady=(0, PAD))
        _heading(sym_card, text="Universe").pack(
            anchor="w", padx=PAD, pady=(PAD, 6))

        # Horizontal scrollable frame for universe tickers
        self._universe_canvas = tk.Canvas(
            sym_card, height=32, bg=BG_WIDGET,
            highlightthickness=0, bd=0)
        self._universe_canvas.pack(fill="x", padx=PAD, pady=(0, 4))
        self._universe_inner = ctk.CTkFrame(
            self._universe_canvas, fg_color="transparent")
        self._universe_canvas_window = self._universe_canvas.create_window(
            (0, 0), window=self._universe_inner, anchor="nw")
        self._universe_inner.bind(
            "<Configure>",
            lambda e: self._universe_canvas.configure(
                scrollregion=self._universe_canvas.bbox("all")))
        # Mouse wheel horizontal scroll
        self._universe_canvas.bind(
            "<MouseWheel>",
            lambda e: self._universe_canvas.xview_scroll(
                -1 * (e.delta // 120), "units"))
        self._universe_hscroll = ctk.CTkScrollbar(
            sym_card, orientation="horizontal",
            command=self._universe_canvas.xview,
            height=8, fg_color=BG_WIDGET,
            button_color=BORDER_SUBTLE,
            button_hover_color=TEXT_SECONDARY,
        )
        self._universe_hscroll.pack(fill="x", padx=PAD, pady=(0, PAD + 4))
        self._universe_canvas.configure(xscrollcommand=self._universe_hscroll.set)

        self._universe_buttons: dict[str, ctk.CTkButton] = {}
        self._universe_symbols: list[str] = []

        _label(self._universe_inner, text="Loading…", font=FONT_MONO_SM,
               text_color=TEXT_SECONDARY).pack(side="left")

    def _check_sidebar_scroll(self) -> None:
        """Show scrollbar only when sidebar content overflows."""
        try:
            canvas = self._sidebar_scroll._parent_canvas
            canvas.update_idletasks()
            bbox = canvas.bbox("all")
            if bbox:
                content_h = bbox[3] - bbox[1]
                visible_h = canvas.winfo_height()
                if content_h > visible_h:
                    self._sidebar_scroll._scrollbar.grid()
                else:
                    self._sidebar_scroll._scrollbar.grid_remove()
        except Exception:
            pass

    # ---- MAIN CONTENT (TabView) ----
    def _build_main_content(self, parent: ctk.CTkFrame) -> None:
        tabs = ctk.CTkTabview(
            parent, fg_color=BG_WIDGET, corner_radius=CORNER_R,
            segmented_button_fg_color=BG_INPUT,
            segmented_button_selected_color="#2b3a42",
            segmented_button_selected_hover_color="#334950",
            segmented_button_unselected_color=BG_INPUT,
            segmented_button_unselected_hover_color=BG_HOVER,
            text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        tabs.grid(row=0, column=1, sticky="nsew")

        tab_overview = tabs.add("  Overview  ")
        tab_strategy = tabs.add("  AI Strategy  ")
        tab_chat = tabs.add("  AI Chat  ")
        tab_journal = tabs.add("  Journal  ")
        tab_logs = tabs.add("  Full Logs  ")
        tab_settings = tabs.add("  ⚙ Settings  ")

        self._build_tab_overview(tab_overview)
        self._build_tab_strategy(tab_strategy)
        self._build_tab_chat(tab_chat)
        self._build_tab_journal(tab_journal)
        self._build_tab_logs(tab_logs)
        self._build_tab_settings(tab_settings)

    # ── Overview tab ──
    def _build_tab_overview(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(0, weight=3)
        tab.grid_rowconfigure(1, weight=2)
        tab.grid_columnconfigure(0, weight=1)

        # Chart
        chart_card = _card(tab)
        chart_card.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 4))
        chart_card.grid_rowconfigure(1, weight=1)
        chart_card.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(chart_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 4))
        _heading(hdr, text="Real-time Market Data").pack(side="left")

        # Ticker search bar with autocomplete
        search_wrapper = ctk.CTkFrame(hdr, fg_color="transparent")
        search_wrapper.pack(side="left", padx=(16, 0))
        self._ticker_search_var = ctk.StringVar()
        self._ticker_search_entry = ctk.CTkEntry(
            search_wrapper, textvariable=self._ticker_search_var,
            width=120, height=26, font=FONT_MONO_SM,
            placeholder_text="Search ticker…",
            fg_color=BG_INPUT, border_color=BORDER_SUBTLE,
            text_color=TEXT_PRIMARY,
        )
        self._ticker_search_entry.pack(side="left")
        self._ticker_search_entry.bind("<KeyRelease>", self._on_ticker_search)
        self._ticker_search_entry.bind("<Return>", self._on_ticker_search_select)
        self._ticker_search_entry.bind("<FocusOut>",
                                        lambda e: self.after(200, self._hide_ticker_dropdown))

        # Dropdown listbox for search results (hidden by default)
        self._ticker_dropdown = ctk.CTkFrame(
            chart_card, fg_color=BG_WIDGET, corner_radius=6,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        self._ticker_dropdown_buttons: list[ctk.CTkButton] = []
        # Will be placed with .place() when results appear

        # Multi-timeframe selector
        tf_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        tf_frame.pack(side="right")
        self._tf_buttons: dict[str, ctk.CTkButton] = {}
        for tf in ["1min", "5min", "15min", "1h", "1d", "1w", "1mo", "1y"]:
            btn = ctk.CTkButton(
                tf_frame, text=tf, width=48, height=24, corner_radius=4,
                fg_color=BG_INPUT if tf != "1h" else "#2b3a42",
                hover_color=BG_HOVER, text_color=TEXT_SECONDARY,
                font=FONT_MONO_SM,
                command=lambda t=tf: self._on_timeframe_change(t),
            )
            btn.pack(side="left", padx=1)
            self._tf_buttons[tf] = btn

        chart_container = ctk.CTkFrame(chart_card, fg_color=BG_PRIMARY,
                                       corner_radius=8)
        chart_container.grid(row=1, column=0, sticky="nsew",
                             padx=PAD, pady=(0, PAD))

        self._fig = Figure(figsize=(10, 3.5), dpi=100, facecolor=BG_PRIMARY)
        self._ax = self._fig.add_subplot(111)
        self._style_ax()
        self._fig.tight_layout(pad=1.5)

        self._canvas = FigureCanvasTkAgg(self._fig, master=chart_container)
        self._canvas.get_tk_widget().configure(bg=BG_PRIMARY, highlightthickness=0)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)

        # Data Feed
        feed_card = _card(tab)
        feed_card.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        feed_card.grid_rowconfigure(1, weight=1)
        feed_card.grid_columnconfigure(0, weight=1)

        _heading(feed_card, text="AI Data Feed").grid(
            row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 4))

        self._feed_text = ctk.CTkTextbox(
            feed_card, font=FONT_MONO_SM, wrap="word",
            fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            corner_radius=8, border_width=0,
        )
        self._feed_text.grid(row=1, column=0, sticky="nsew",
                             padx=PAD, pady=(0, PAD))
        self._feed_text.configure(state="disabled")

        tw = self._feed_text._textbox
        tw.tag_configure("grok_fast", foreground=ACCENT_CYAN)
        tw.tag_configure("grok", foreground=ACCENT_GOLD)
        tw.tag_configure("opus", foreground=ACCENT_PURPLE)
        tw.tag_configure("system", foreground=TEXT_SECONDARY)
        tw.tag_configure("error", foreground=ACCENT_RED)
        tw.tag_configure("ts", foreground=TEXT_SECONDARY)
        tw.tag_configure("thinking", foreground=ACCENT_GOLD, font=FONT_MONO_SM)
        tw.tag_configure("streaming", foreground=TEXT_SECONDARY, font=FONT_MONO_SM)

        # Track AI streaming state (for line-by-line reasoning display)
        self._ai_streaming_agent: Optional[str] = None

    # ── AI Strategy tab ──
    def _build_tab_strategy(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        # Strategy panel
        strat_card = _card(tab)
        strat_card.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 4))
        strat_card.grid_rowconfigure(1, weight=1)
        strat_card.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(strat_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 4))
        _heading(hdr, text="Opus CEO — Strategy Engine").pack(side="left")

        # Confidence gauge
        self._confidence_label = ctk.CTkLabel(
            hdr, text="Confidence: —", font=FONT_MONO_SM,
            text_color=TEXT_SECONDARY)
        self._confidence_label.pack(side="right", padx=(8, 0))

        self._strategy_badge = ctk.CTkLabel(
            hdr, text="  v0  ", font=FONT_MONO_SM,
            fg_color=BG_INPUT, text_color=ACCENT_CYAN, corner_radius=4)
        self._strategy_badge.pack(side="right")

        self._strategy_text = ctk.CTkTextbox(
            strat_card, font=FONT_MONO, wrap="word",
            fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            corner_radius=8, border_width=0,
        )
        self._strategy_text.grid(row=1, column=0, sticky="nsew",
                                 padx=PAD, pady=(0, PAD))
        self._strategy_text.configure(state="disabled")

        self._strategy_text._textbox.tag_configure(
            "heading", foreground=ACCENT_CYAN,
            font=("JetBrains Mono", 12, "bold"))
        self._strategy_text._textbox.tag_configure(
            "risk", foreground=ACCENT_AMBER)
        self._strategy_text._textbox.tag_configure(
            "cost", foreground=TEXT_SECONDARY)
        self._strategy_text._textbox.tag_configure(
            "accepted", foreground=ACCENT_GREEN)
        self._strategy_text._textbox.tag_configure(
            "rejected", foreground=ACCENT_RED)

        # Self-correction panel
        review_card = _card(tab)
        review_card.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        review_card.grid_rowconfigure(1, weight=1)
        review_card.grid_columnconfigure(0, weight=1)

        _heading(review_card, text="Self-Correction Log").grid(
            row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 4))

        self._review_text = ctk.CTkTextbox(
            review_card, font=FONT_MONO_SM, wrap="word",
            fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            corner_radius=8, border_width=0,
        )
        self._review_text.grid(row=1, column=0, sticky="nsew",
                               padx=PAD, pady=(0, PAD))
        self._review_text.configure(state="disabled")

        self._review_text._textbox.tag_configure(
            "version", foreground=ACCENT_CYAN)
        self._review_text._textbox.tag_configure(
            "sep", foreground=BORDER_SUBTLE)

    # ── AI Chat tab ──
    def _build_tab_chat(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=0)
        tab.grid_columnconfigure(0, weight=1)

        chat_card = _card(tab)
        chat_card.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 4))
        chat_card.grid_rowconfigure(1, weight=1)
        chat_card.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(chat_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 4))
        _heading(hdr, text="Chat with Opus CEO").pack(side="left")
        _label(hdr, text="Ask about strategy, positions, or market conditions",
               font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(side="right")

        self._chat_display = ctk.CTkTextbox(
            chat_card, font=FONT_MONO, wrap="word",
            fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            corner_radius=8, border_width=0,
        )
        self._chat_display.grid(row=1, column=0, sticky="nsew",
                                padx=PAD, pady=(0, PAD))
        self._chat_display.configure(state="disabled")

        tw = self._chat_display._textbox
        tw.tag_configure("user_label", foreground=ACCENT_GOLD,
                        font=("JetBrains Mono", 11, "bold"))
        tw.tag_configure("opus_label", foreground=ACCENT_PURPLE,
                        font=("JetBrains Mono", 11, "bold"))
        tw.tag_configure("user_msg", foreground=TEXT_HEADING)
        tw.tag_configure("opus_msg", foreground=TEXT_PRIMARY)
        tw.tag_configure("ts", foreground=TEXT_SECONDARY)

        # Input area
        input_frame = ctk.CTkFrame(tab, fg_color="transparent")
        input_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))
        input_frame.grid_columnconfigure(0, weight=1)

        self._chat_input = ctk.CTkEntry(
            input_frame, placeholder_text="Type a message to Opus CEO…",
            font=FONT_MONO, fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            border_color=BORDER_SUBTLE, corner_radius=8, height=40,
        )
        self._chat_input.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._chat_input.bind("<Return>", lambda e: self._on_chat_send())

        self._chat_send_btn = ctk.CTkButton(
            input_frame, text="Send", width=80, height=40, corner_radius=8,
            fg_color="#2b3a42", hover_color="#334950",
            text_color=ACCENT_CYAN, font=("Pretendard", 12, "bold"),
            command=self._on_chat_send,
        )
        self._chat_send_btn.grid(row=0, column=1)

    # ── Journal tab ──
    def _build_tab_journal(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        journal_card = _card(tab)
        journal_card.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        journal_card.grid_rowconfigure(1, weight=1)
        journal_card.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(journal_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 4))
        _heading(hdr, text="Trade Journal").pack(side="left")

        refresh_btn = ctk.CTkButton(
            hdr, text="⟳ Refresh", width=80, height=28, corner_radius=6,
            fg_color=BG_INPUT, hover_color=BG_HOVER,
            text_color=TEXT_SECONDARY, font=FONT_SMALL,
            command=self._refresh_journal,
        )
        refresh_btn.pack(side="right")

        self._journal_text = ctk.CTkTextbox(
            journal_card, font=FONT_MONO, wrap="word",
            fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            corner_radius=8, border_width=0,
        )
        self._journal_text.grid(row=1, column=0, sticky="nsew",
                                padx=PAD, pady=(0, PAD))
        self._journal_text.configure(state="disabled")

        tw = self._journal_text._textbox
        tw.tag_configure("date", foreground=ACCENT_CYAN,
                        font=("JetBrains Mono", 12, "bold"))
        tw.tag_configure("win", foreground=ACCENT_GREEN)
        tw.tag_configure("loss", foreground=ACCENT_RED)
        tw.tag_configure("sep", foreground=BORDER_SUBTLE)
        tw.tag_configure("lesson", foreground=ACCENT_AMBER)

    # ── Full Logs tab ──
    def _build_tab_logs(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        log_card = _card(tab)
        log_card.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        log_card.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 4))
        _heading(hdr, text="System Logs").pack(side="left")
        self._log_count_label = _label(
            hdr, text="0 entries", font=FONT_SMALL, text_color=TEXT_SECONDARY)
        self._log_count_label.pack(side="right")

        self._log_text = ctk.CTkTextbox(
            log_card, font=FONT_MONO_SM, wrap="word",
            fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            corner_radius=8, border_width=0,
        )
        self._log_text.grid(row=1, column=0, sticky="nsew",
                            padx=PAD, pady=(0, PAD))
        self._log_text.configure(state="disabled")

        tw = self._log_text._textbox
        tw.tag_configure("info", foreground=ACCENT_GREEN)
        tw.tag_configure("warn", foreground=ACCENT_AMBER)
        tw.tag_configure("error", foreground=ACCENT_RED)
        tw.tag_configure("strategy", foreground=ACCENT_CYAN)
        tw.tag_configure("ts", foreground=TEXT_SECONDARY)
        tw.tag_configure("dim", foreground=TEXT_SECONDARY)

        self._log_entry_count = 0

    # ── Settings tab ──
    def _build_tab_settings(self, tab: ctk.CTkFrame) -> None:
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        # Outer scroll in case window is small
        outer = ctk.CTkScrollableFrame(
            tab, fg_color="transparent",
            scrollbar_button_color=BG_PRIMARY,
            scrollbar_button_hover_color=BG_HOVER,
        )
        outer.pack(fill="both", expand=True, padx=4, pady=4)

        self._settings_edit_mode = False
        self._settings_fields: dict[str, Any] = {}

        # ── Header row ──
        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 10))
        _heading(hdr, text="⚙  Environment Settings").pack(side="left")

        # Sync indicator
        self._sync_indicator = ctk.CTkLabel(
            hdr, text="●  Server Synced", font=FONT_MONO_SM,
            text_color=ACCENT_GREEN)
        self._sync_indicator.pack(side="right", padx=(0, 8))

        # ── Action buttons ──
        btn_bar = ctk.CTkFrame(outer, fg_color="transparent")
        btn_bar.pack(fill="x", pady=(0, 12))

        self._settings_edit_btn = ctk.CTkButton(
            btn_bar, text="🔓 Edit Mode", width=120, height=32,
            corner_radius=6, fg_color="#2b3a42", hover_color="#334950",
            text_color=ACCENT_CYAN, font=("Pretendard", 11, "bold"),
            command=self._toggle_settings_edit,
        )
        self._settings_edit_btn.pack(side="left", padx=(0, 6))

        self._settings_save_btn = ctk.CTkButton(
            btn_bar, text="💾 Save & Apply", width=130, height=32,
            corner_radius=6, fg_color="#1b4332", hover_color="#2d6a4f",
            text_color=ACCENT_GREEN, font=("Pretendard", 11, "bold"),
            command=self._save_settings, state="disabled",
        )
        self._settings_save_btn.pack(side="left", padx=(0, 6))

        self._settings_reset_btn = ctk.CTkButton(
            btn_bar, text="↺ Reset to Default", width=140, height=32,
            corner_radius=6, fg_color="#3b1219", hover_color="#5c1a27",
            text_color=ACCENT_RED, font=("Pretendard", 11, "bold"),
            command=self._reset_settings, state="disabled",
        )
        self._settings_reset_btn.pack(side="left")

        # ──────────────────────────────────────────────────────────────
        # SECTION 1: Trading Mode
        # ──────────────────────────────────────────────────────────────
        s1 = _card(outer)
        s1.pack(fill="x", pady=(0, 10))
        _heading(s1, text="Trading Mode").pack(anchor="w", padx=PAD, pady=(PAD, 6))

        mode_row = ctk.CTkFrame(s1, fg_color="transparent")
        mode_row.pack(fill="x", padx=PAD, pady=(0, PAD))
        _label(mode_row, text="Paper (simulated)  /  Live (real money)",
               font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(side="left")

        self._mode_switch_var = ctk.StringVar(value="paper")
        self._mode_switch = ctk.CTkSegmentedButton(
            mode_row, values=["paper", "live"], width=180, height=30,
            font=("JetBrains Mono", 11, "bold"),
            fg_color=BG_INPUT, selected_color="#2b3a42",
            selected_hover_color="#334950",
            unselected_color=BG_INPUT, unselected_hover_color=BG_HOVER,
            text_color=TEXT_PRIMARY,
            variable=self._mode_switch_var, state="disabled",
        )
        self._mode_switch.pack(side="right")
        self._settings_fields["trading_mode"] = self._mode_switch_var

        ctk.CTkFrame(s1, fg_color="transparent", height=PAD).pack(fill="x")

        # ──────────────────────────────────────────────────────────────
        # SECTION 2: Risk Management
        # ──────────────────────────────────────────────────────────────
        s2 = _card(outer)
        s2.pack(fill="x", pady=(0, 10))
        _heading(s2, text="Risk Management").pack(anchor="w", padx=PAD, pady=(PAD, 6))

        risk_fields = [
            ("max_position_percent", "Max Position Size (%)", 0.1, 100.0),
            ("max_drawdown_percent", "Max Drawdown (%)", 0.1, 100.0),
            ("daily_loss_limit_percent", "Daily Loss Limit (%)", 0.1, 100.0),
            ("daily_ai_budget_usd", "Daily AI Budget ($)", 0.01, 100000.0),
            ("consecutive_stop_loss_pause", "Stop-Loss → Pause (count)", 1, 100),
            ("vix_panic_threshold", "VIX Panic Threshold", 1.0, 100.0),
        ]
        for key, label_text, lo, hi in risk_fields:
            self._add_number_field(s2, key, label_text, lo, hi)

        # Adaptive stop-loss (merged into Risk Management)
        _label(s2, text="AI uses ATR-based stop-loss instead of fixed %.",
               font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(
            anchor="w", padx=PAD, pady=(8, 4))
        self._add_toggle_field(s2, "enable_adaptive_stoploss",
                               "Enable Adaptive Stop-Loss")
        self._add_number_field(s2, "adaptive_stoploss_hard_cap_pct",
                               "Hard Cap — Max Stop-Loss (%)", 0.5, 50.0)

        ctk.CTkFrame(s2, fg_color="transparent", height=PAD).pack(fill="x")

        # ──────────────────────────────────────────────────────────────
        # SECTION 3: AI Model Pairing
        # ──────────────────────────────────────────────────────────────
        s3 = _card(outer)
        s3.pack(fill="x", pady=(0, 10))
        _heading(s3, text="AI Model Pairing").pack(anchor="w", padx=PAD, pady=(PAD, 6))

        # Display name → API model ID mapping
        self._ai_model_map: dict[str, str] = {
            "Grok 4.1 Fast": "grok-3-fast",
            "Grok 4.2": "grok-3",
            "Claude Haiku 4.5": "claude-haiku-4-5-20250514",
            "Claude Opus 4.6": "claude-opus-4-20250514",
        }
        self._ai_model_reverse: dict[str, str] = {
            v: k for k, v in self._ai_model_map.items()
        }
        model_display_names = list(self._ai_model_map.keys())

        ai_roles = [
            ("ai_model_scan", "Data Scan (Fast)"),
            ("ai_model_strategy", "Strategy Brainstorm"),
            ("ai_model_ceo", "CEO Decision Maker"),
        ]
        for key, label_text in ai_roles:
            self._add_dropdown_field(s3, key, label_text, model_display_names)

        ctk.CTkFrame(s3, fg_color="transparent", height=PAD).pack(fill="x")

        # ──────────────────────────────────────────────────────────────
        # SECTION 4: Ticker Universe
        # ──────────────────────────────────────────────────────────────
        s4 = _card(outer)
        s4.pack(fill="x", pady=(0, 10))
        _heading(s4, text="Ticker Universe").pack(anchor="w", padx=PAD, pady=(PAD, 6))

        dyn_row = ctk.CTkFrame(s4, fg_color="transparent")
        dyn_row.pack(fill="x", padx=PAD, pady=(0, 6))
        _label(dyn_row, text="Dynamic Universe (AI auto-select)",
               font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(side="left")
        self._dyn_universe_var = ctk.StringVar(value="on")
        self._dyn_universe_switch = ctk.CTkSwitch(
            dyn_row, text="", variable=self._dyn_universe_var,
            onvalue="on", offvalue="off",
            fg_color=BG_INPUT, progress_color=ACCENT_CYAN,
            button_color=TEXT_SECONDARY, button_hover_color=TEXT_PRIMARY,
            state="disabled",
            command=self._on_dynamic_universe_toggle,
        )
        self._dyn_universe_switch.pack(side="right")
        self._settings_fields["enable_dynamic_universe"] = self._dyn_universe_var

        self._add_number_field(s4, "dynamic_universe_size",
                               "Universe Size (tickers)", 1, 100)

        ticker_row = ctk.CTkFrame(s4, fg_color="transparent")
        ticker_row.pack(fill="x", padx=PAD, pady=(0, 4))
        self._fixed_tickers_label = _label(
            ticker_row, text="Fixed Tickers", font=FONT_SMALL,
            text_color=TEXT_SECONDARY)
        self._fixed_tickers_label.pack(anchor="w")

        # Tag display area (flow-wrap buttons)
        self._ticker_tags_frame = ctk.CTkFrame(ticker_row, fg_color="transparent")
        self._ticker_tags_frame.pack(fill="x", pady=(2, 0))
        self._ticker_tags: list[str] = []  # list of added tickers
        self._ticker_tag_widgets: dict[str, ctk.CTkFrame] = {}

        # Input entry for adding new tickers
        self._fixed_tickers_entry = ctk.CTkEntry(
            ticker_row, placeholder_text="Type ticker + Enter…",
            font=FONT_MONO, fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            border_color=BORDER_SUBTLE, corner_radius=6, height=32,
            state="disabled",
        )
        self._fixed_tickers_entry.pack(fill="x", pady=(4, 0))
        self._fixed_tickers_entry.bind("<Return>", self._on_ticker_tag_enter)
        self._fixed_tickers_entry.bind("<KeyRelease>", self._on_ticker_tag_validate)
        # Focus highlight
        self._fixed_tickers_entry.bind(
            "<FocusIn>",
            lambda e: self._fixed_tickers_label.configure(text_color=TEXT_HEADING))
        self._fixed_tickers_entry.bind(
            "<FocusOut>",
            lambda e: self._fixed_tickers_label.configure(text_color=TEXT_SECONDARY))

        # Warning label below entry
        self._ticker_warning_label = _label(
            ticker_row, text="", font=("Pretendard", 10),
            text_color=ACCENT_RED)
        self._ticker_warning_label.pack(anchor="w", pady=(2, 0))
        self._ticker_warning_active = False

        self._settings_fields["fixed_tickers"] = self._fixed_tickers_entry

        _label(s4, text="Universe Filtering (applied by Opus CEO)",
               font=FONT_SMALL, text_color=ACCENT_CYAN).pack(
            anchor="w", padx=PAD, pady=(8, 4))
        self._add_number_field(s4, "universe_min_market_cap_usd",
                               "Min Market Cap ($)", 0, 1e15)
        self._add_number_field(s4, "universe_min_volume_usd",
                               "Min Daily Volume ($)", 0, 1e12)

        ctk.CTkFrame(s4, fg_color="transparent", height=PAD).pack(fill="x")

        # ──────────────────────────────────────────────────────────────
        # SECTION 5: Chart Settings
        # ──────────────────────────────────────────────────────────────
        s5 = _card(outer)
        s5.pack(fill="x", pady=(0, 10))
        _heading(s5, text="Chart Settings").pack(anchor="w", padx=PAD, pady=(PAD, 6))

        tf_options = ["1min", "5min", "15min", "1h", "1d", "1w", "1mo", "1y"]
        self._add_dropdown_field(s5, "default_chart_timeframe",
                                 "Default Timeframe", tf_options)
        self._add_number_field(s5, "default_candle_count",
                               "Candle Count", 10, 1000)

        ctk.CTkFrame(s5, fg_color="transparent", height=PAD).pack(fill="x")

        # ──────────────────────────────────────────────────────────────
        # SECTION 6: Timezone
        # ──────────────────────────────────────────────────────────────
        s6 = _card(outer)
        s6.pack(fill="x", pady=(0, 10))
        _heading(s6, text="Display Timezone").pack(anchor="w", padx=PAD, pady=(PAD, 6))

        tz_row = ctk.CTkFrame(s6, fg_color="transparent")
        tz_row.pack(fill="x", padx=PAD, pady=(0, PAD))
        _label(tz_row, text="Timezone", font=FONT_SMALL,
               text_color=TEXT_SECONDARY).pack(side="left")

        # Full list stored for validation; dropdown shows curated common set
        self._all_timezones = sorted(pytz.common_timezones)
        _common_tz = [
            "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
            "UTC", "Europe/London", "Europe/Berlin", "Europe/Paris",
            "Europe/Moscow", "Asia/Tokyo", "Asia/Seoul", "Asia/Shanghai",
            "Asia/Hong_Kong", "Asia/Singapore", "Asia/Kolkata",
            "Asia/Dubai", "Australia/Sydney", "Pacific/Auckland",
            "America/New_York", "America/Chicago", "America/Denver",
            "America/Los_Angeles", "America/Sao_Paulo", "America/Toronto",
            "Africa/Johannesburg", "Africa/Cairo",
        ]
        # Ensure all common ones are valid pytz zones
        tz_display = [z for z in _common_tz if z in self._all_timezones]

        self._tz_var = ctk.StringVar(value="Asia/Seoul")
        self._tz_combo = ctk.CTkComboBox(
            tz_row, values=tz_display, variable=self._tz_var,
            width=260, height=30, font=FONT_MONO_SM,
            fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            border_color=BORDER_SUBTLE, button_color=BG_HOVER,
            button_hover_color="#495057", dropdown_fg_color=BG_WIDGET,
            dropdown_text_color=TEXT_PRIMARY,
            dropdown_hover_color=BG_HOVER, corner_radius=6,
            state="disabled",
        )
        self._tz_combo.pack(side="right")
        # Make timezone combo read-only (prevent text editing)
        try:
            self._tz_combo._entry.configure(state="readonly")
        except (AttributeError, tk.TclError):
            pass
        self._settings_fields["display_timezone"] = self._tz_var

        _label(s6, text="Type any pytz timezone name or pick from the list above.",
               font=("Pretendard", 10), text_color=TEXT_SECONDARY).pack(
            anchor="w", padx=PAD, pady=(0, PAD))

        # ──────────────────────────────────────────────────────────────
        # SECTION 7: Advanced (Volatility / Confidence / Social)
        # ──────────────────────────────────────────────────────────────
        s7 = _card(outer)
        s7.pack(fill="x", pady=(0, 10))
        _heading(s7, text="Advanced Thresholds").pack(
            anchor="w", padx=PAD, pady=(PAD, 6))

        adv_fields = [
            ("price_change_threshold_pct", "Price Change Alert (%)", 0.01, 100.0),
            ("volume_spike_multiplier", "Volume Spike Multiplier", 1.0, 100.0),
            ("sentiment_drop_threshold_pct", "Sentiment Drop Alert (%)", 1.0, 100.0),
            ("individual_drawdown_pct", "Individual Drawdown (%)", 0.01, 100.0),
            ("high_confidence_threshold", "High Confidence (0-1)", 0.0, 1.0),
            ("low_confidence_threshold", "Low Confidence (0-1)", 0.0, 1.0),
            ("high_confidence_position_mult", "High-Conf Position Mult", 0.01, 10.0),
            ("low_confidence_position_mult", "Low-Conf Position Mult", 0.01, 10.0),
            ("low_reliability_weight", "Low Reliability Weight (0-1)", 0.0, 1.0),
        ]
        for key, label_text, lo, hi in adv_fields:
            self._add_number_field(s7, key, label_text, lo, hi)

        # Strategy intervals
        self._add_number_field(s7, "strategy_update_interval_min",
                               "Strategy Review Interval (min)", 1, 1440)
        self._add_number_field(s7, "grok_scan_interval_min",
                               "Scan Interval (min)", 1, 1440)

        # Toggles
        for toggle_key, toggle_label in [
            ("allow_extended_hours", "Allow Extended Hours"),
            ("enable_prompt_caching", "Enable Prompt Caching"),
            ("db_backup_enabled", "Database Backup"),
            ("social_noise_filter_enabled", "Social Noise Filter"),
        ]:
            self._add_toggle_field(s7, toggle_key, toggle_label)

        ctk.CTkFrame(s7, fg_color="transparent", height=PAD).pack(fill="x")

        # ──────────────────────────────────────────────────────────────
        # SECTION 8: API Key Input (hybrid) + Read-only status
        # ──────────────────────────────────────────────────────────────
        s8 = _card(outer)
        s8.pack(fill="x", pady=(0, 10))
        _heading(s8, text="API Keys (Hybrid: input here → stored on server)").pack(
            anchor="w", padx=PAD, pady=(PAD, 6))
        _label(s8, text="Keys are sent to the server and erased from client memory.",
               font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(
            anchor="w", padx=PAD, pady=(0, 8))

        self._api_key_entries: dict[str, ctk.CTkEntry] = {}
        for key_name, display_label in [
            ("ANTHROPIC_API_KEY", "Anthropic (Claude)"),
            ("XAI_GROK_API_KEY", "xAI (Grok)"),
            ("POLYGON_API_KEY", "Polygon.io"),
            ("APCA_API_KEY_ID", "Alpaca Key ID"),
            ("APCA_API_SECRET_KEY", "Alpaca Secret"),
        ]:
            r = ctk.CTkFrame(s8, fg_color="transparent")
            r.pack(fill="x", padx=PAD, pady=2)
            _label(r, text=display_label, font=FONT_SMALL,
                   text_color=TEXT_SECONDARY, width=140).pack(side="left")
            entry = ctk.CTkEntry(
                r, placeholder_text="Paste key here…", show="•",
                font=FONT_MONO_SM, fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
                border_color=BORDER_SUBTLE, corner_radius=6, height=28,
                state="disabled",
            )
            entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
            self._api_key_entries[key_name] = entry

        key_btn_row = ctk.CTkFrame(s8, fg_color="transparent")
        key_btn_row.pack(fill="x", padx=PAD, pady=(8, 4))
        self._submit_keys_btn = ctk.CTkButton(
            key_btn_row, text="🔑 Apply Keys to Server", width=180, height=30,
            corner_radius=6, fg_color="#2b3a42", hover_color="#334950",
            text_color=ACCENT_CYAN, font=("Pretendard", 11, "bold"),
            command=self._submit_api_keys, state="disabled",
        )
        self._submit_keys_btn.pack(side="left")

        # Key status (read-only)
        _label(s8, text="Key Status (read-only)", font=FONT_SMALL,
               text_color=TEXT_SECONDARY).pack(anchor="w", padx=PAD, pady=(8, 4))

        self._key_status_labels: dict[str, ctk.CTkLabel] = {}
        for key_name, display_label in [
            ("ANTHROPIC_API_KEY", "Anthropic"),
            ("XAI_GROK_API_KEY", "xAI Grok"),
            ("POLYGON_API_KEY", "Polygon"),
            ("APCA_API_KEY_ID", "Alpaca ID"),
            ("APCA_API_SECRET_KEY", "Alpaca Secret"),
        ]:
            r = ctk.CTkFrame(s8, fg_color="transparent")
            r.pack(fill="x", padx=PAD, pady=1)
            _label(r, text=f"{display_label}:", font=FONT_SMALL,
                   text_color=TEXT_SECONDARY, width=100).pack(side="left")
            status_lbl = _label(r, text="checking…", font=FONT_MONO_SM,
                                text_color=TEXT_SECONDARY)
            status_lbl.pack(side="left", padx=(8, 0))
            self._key_status_labels[key_name] = status_lbl

        ctk.CTkFrame(s8, fg_color="transparent", height=PAD).pack(fill="x")

        # Bottom spacer for overall settings content
        ctk.CTkFrame(outer, fg_color="transparent", height=40).pack(fill="x")

        # Initial load
        self.after(600, self._load_settings_from_server)
        self.after(800, self._load_key_status)

    # ── Settings tab: helpers ──────────────────────────────────────────

    # Keys whose values may use K/M/B/T suffix notation
    _HUMAN_NUMBER_KEYS = {
        "universe_min_market_cap_usd", "universe_min_volume_usd",
        "daily_ai_budget_usd",
    }

    def _add_number_field(self, parent: Any, key: str, label_text: str,
                          lo: float, hi: float) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=PAD, pady=2)
        lbl = _label(row, text=label_text, font=FONT_SMALL,
                     text_color=TEXT_SECONDARY)
        lbl.pack(side="left")
        var = ctk.StringVar(value="")
        entry = ctk.CTkEntry(
            row, textvariable=var, width=100, height=28,
            font=FONT_MONO_SM, fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            border_color=BORDER_SUBTLE, corner_radius=6, state="disabled",
        )
        entry.pack(side="right")
        # Focus-highlight: brighten label on focus, dim on blur
        entry.bind("<FocusIn>",
                   lambda e, lb=lbl: lb.configure(text_color=TEXT_HEADING))
        entry.bind("<FocusOut>",
                   lambda e, lb=lbl: lb.configure(text_color=TEXT_SECONDARY))

        # Input validation: numbers (and optional K/M/B/T suffix)
        allows_suffix = key in self._HUMAN_NUMBER_KEYS
        self._bind_number_validation(entry, allows_suffix)

        # Auto-format large numbers on FocusOut for K/M/B/T fields
        if allows_suffix:
            entry.bind("<FocusOut>", lambda e, v=var, lb=lbl: (
                self._auto_format_human_number(v),
                lb.configure(text_color=TEXT_SECONDARY),
            ))

        self._settings_fields[key] = (var, entry, lo, hi)

    def _bind_number_validation(self, entry: ctk.CTkEntry,
                                allow_suffix: bool = False) -> None:
        """Restrict entry to digits, '.', and optionally K/M/B/T."""
        allowed = set("0123456789.")
        if allow_suffix:
            allowed |= set("KkMmBbTt")
        try:
            inner = entry._entry
        except AttributeError:
            return

        def _filter(event: Any) -> str | None:
            char = event.char
            if not char or char in ('\x08', '\x7f', '\r', '\n'):
                return None  # allow control keys
            if char not in allowed:
                return "break"
            return None

        inner.bind("<KeyPress>", _filter)

    def _auto_format_human_number(self, var: ctk.StringVar) -> None:
        """Auto-format the value in a StringVar to human-readable (e.g. 1000000 → 1M)."""
        raw = var.get().strip()
        if not raw:
            return
        num = parse_human_number(raw)
        if num is not None and abs(num) >= 1000:
            var.set(format_human_number(num))

    def _add_dropdown_field(self, parent: Any, key: str, label_text: str,
                            options: list[str]) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=PAD, pady=2)
        lbl = _label(row, text=label_text, font=FONT_SMALL,
                     text_color=TEXT_SECONDARY)
        lbl.pack(side="left")
        var = ctk.StringVar(value=options[0])
        combo = ctk.CTkComboBox(
            row, values=options, variable=var,
            width=180, height=28, font=FONT_MONO_SM,
            fg_color=BG_INPUT, text_color=TEXT_PRIMARY,
            border_color=BORDER_SUBTLE, button_color=BG_HOVER,
            button_hover_color="#495057", dropdown_fg_color=BG_WIDGET,
            dropdown_text_color=TEXT_PRIMARY,
            dropdown_hover_color=BG_HOVER, corner_radius=6,
            state="disabled",
        )
        combo.pack(side="right")
        # Make combobox read-only (prevent text editing)
        try:
            combo._entry.configure(state="readonly")
        except (AttributeError, tk.TclError):
            pass
        # Focus-highlight on the inner entry of the combobox
        try:
            inner = combo._entry
            inner.bind("<FocusIn>",
                       lambda e, lb=lbl: lb.configure(text_color=TEXT_HEADING))
            inner.bind("<FocusOut>",
                       lambda e, lb=lbl: lb.configure(text_color=TEXT_SECONDARY))
        except AttributeError:
            pass
        self._settings_fields[key] = (var, combo)

    def _add_toggle_field(self, parent: Any, key: str,
                          label_text: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=PAD, pady=2)
        lbl = _label(row, text=label_text, font=FONT_SMALL,
                     text_color=TEXT_SECONDARY)
        lbl.pack(side="left")
        var = ctk.StringVar(value="on")
        switch = ctk.CTkSwitch(
            row, text="", variable=var, onvalue="on", offvalue="off",
            fg_color=BG_INPUT, progress_color=ACCENT_CYAN,
            button_color=TEXT_SECONDARY, button_hover_color=TEXT_PRIMARY,
            state="disabled",
            command=lambda lb=lbl: self.after(
                50, lambda: lb.configure(text_color=TEXT_HEADING
                                         if var.get() == "on" else TEXT_SECONDARY)),
        )
        switch.pack(side="right")
        self._settings_fields[key] = (var, switch)

    # ── Settings tab: edit-mode toggle ──

    def _toggle_settings_edit(self) -> None:
        self._settings_edit_mode = not self._settings_edit_mode
        new_state = "normal" if self._settings_edit_mode else "disabled"

        self._settings_edit_btn.configure(
            text="🔒 Lock" if self._settings_edit_mode else "🔓 Edit Mode",
            fg_color="#3b2a10" if self._settings_edit_mode else "#2b3a42",
            text_color=ACCENT_GOLD if self._settings_edit_mode else ACCENT_CYAN,
        )
        self._settings_save_btn.configure(state=new_state)
        self._settings_reset_btn.configure(state=new_state)
        self._mode_switch.configure(state=new_state)
        self._dyn_universe_switch.configure(state=new_state)
        self._tz_combo.configure(state=new_state)
        # Keep timezone combo entry read-only even when enabled
        if self._settings_edit_mode:
            try:
                self._tz_combo._entry.configure(state="readonly")
            except (AttributeError, tk.TclError):
                pass

        # Number / dropdown / toggle fields
        for key, field_data in self._settings_fields.items():
            if key in ("trading_mode", "enable_dynamic_universe",
                       "display_timezone"):
                continue  # handled above
            if key == "fixed_tickers":
                # Respect dynamic universe toggle when in edit mode
                if self._settings_edit_mode:
                    dyn_on = self._dyn_universe_var.get() == "on"
                    self._fixed_tickers_entry.configure(
                        state="disabled" if dyn_on else "normal")
                else:
                    self._fixed_tickers_entry.configure(state="disabled")
                continue
            if isinstance(field_data, tuple):
                if len(field_data) == 4:
                    _, entry, _, _ = field_data
                    entry.configure(state=new_state)
                elif len(field_data) == 2:
                    _, widget = field_data
                    widget.configure(state=new_state)
                    # Keep combo entries read-only even when enabled
                    if isinstance(widget, ctk.CTkComboBox) and self._settings_edit_mode:
                        try:
                            widget._entry.configure(state="readonly")
                        except (AttributeError, tk.TclError):
                            pass

        # API key entries
        for entry in self._api_key_entries.values():
            entry.configure(state=new_state)
        self._submit_keys_btn.configure(state=new_state)

        # When locking, revert to last saved values from server
        if not self._settings_edit_mode:
            self._load_settings_from_server()

    def _on_dynamic_universe_toggle(self) -> None:
        """Disable Fixed Tickers entry when Dynamic Universe is on."""
        if not self._settings_edit_mode:
            return
        dyn_on = self._dyn_universe_var.get() == "on"
        self._fixed_tickers_entry.configure(
            state="disabled" if dyn_on else "normal")

    # ── Tag-based ticker input ──

    def _rebuild_ticker_tags(self) -> None:
        """Rebuild tag widgets from self._ticker_tags list."""
        for w in self._ticker_tag_widgets.values():
            w.destroy()
        self._ticker_tag_widgets.clear()

        can_delete = (self._settings_edit_mode
                      and self._dyn_universe_var.get() != "on")

        for sym in self._ticker_tags:
            tag = ctk.CTkFrame(self._ticker_tags_frame, fg_color="#2b3a42",
                               corner_radius=4)
            tag.pack(side="left", padx=2, pady=2)
            _label(tag, text=sym, font=FONT_MONO_SM,
                   text_color=ACCENT_CYAN).pack(side="left", padx=(6, 0))
            x_btn = ctk.CTkButton(
                tag, text="✕", width=18, height=18, font=("Pretendard", 9),
                fg_color="transparent", hover_color=BG_HOVER,
                text_color=TEXT_SECONDARY, corner_radius=3,
                command=lambda s=sym: self._remove_ticker_tag(s),
            )
            x_btn.pack(side="left", padx=(2, 4), pady=2)
            if not can_delete:
                x_btn.configure(state="disabled")
            self._ticker_tag_widgets[sym] = tag

    def _remove_ticker_tag(self, symbol: str) -> None:
        """Remove a ticker tag."""
        if symbol in self._ticker_tags:
            self._ticker_tags.remove(symbol)
            self._rebuild_ticker_tags()

    def _on_ticker_tag_enter(self, event=None) -> None:
        """Convert entry text into a tag on Enter press."""
        if self._ticker_warning_active:
            return  # block adding while warning is active
        raw = self._fixed_tickers_entry.get().strip().upper()
        if not raw:
            return
        self._ticker_tags.append(raw)
        self._fixed_tickers_entry.delete(0, "end")
        self._ticker_warning_label.configure(text="")
        self._ticker_warning_active = False
        self._rebuild_ticker_tags()
        # Auto-focus back to entry for continuous input
        self._fixed_tickers_entry.focus_set()

    def _on_ticker_tag_validate(self, event=None) -> None:
        """Live-validate the current input text against cached ticker list."""
        raw = self._fixed_tickers_entry.get().strip().upper()
        if not raw:
            self._ticker_warning_label.configure(text="")
            self._ticker_warning_active = False
            return

        # Check for non-alphabetic characters
        if not raw.isalpha():
            self._ticker_warning_label.configure(
                text="⚠ Invalid: only letters allowed")
            self._ticker_warning_active = True
            return

        # Check for duplicates
        if raw in self._ticker_tags:
            self._ticker_warning_label.configure(
                text=f"⚠ '{raw}' already added")
            self._ticker_warning_active = True
            return

        # Check against cached Alpaca ticker list (async)
        def _check():
            try:
                resp = self._http.get(
                    "/api/tickers/search", params={"q": raw, "limit": 1})
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    if raw not in results:
                        self.after(0, self._set_ticker_warning,
                                   f"⚠ '{raw}' not found in Alpaca")
                        return
            except Exception:
                pass
            self.after(0, self._set_ticker_warning, "")

        threading.Thread(target=_check, daemon=True).start()

    def _set_ticker_warning(self, text: str) -> None:
        self._ticker_warning_label.configure(text=text)
        self._ticker_warning_active = bool(text)

    # ── Settings tab: load from server ──

    def _load_settings_from_server(self) -> None:
        def _fetch() -> None:
            try:
                resp = self._http.get("/api/settings")
                if resp.status_code == 200:
                    data = resp.json()
                    self.after(0, self._apply_settings_to_ui,
                              data.get("settings", {}))
            except Exception:
                self.after(0, self._set_sync_indicator, False)

        threading.Thread(target=_fetch, daemon=True).start()

    def _apply_settings_to_ui(self, data: dict) -> None:
        """Populate fields from server data."""
        for key, field_data in self._settings_fields.items():
            val = data.get(key)
            if val is None:
                continue

            if key == "trading_mode":
                self._mode_switch_var.set(str(val))
            elif key == "enable_dynamic_universe":
                self._dyn_universe_var.set("on" if val else "off")
            elif key == "display_timezone":
                self._tz_var.set(str(val))
            elif key == "fixed_tickers":
                # Populate tag-based ticker list
                if isinstance(val, list):
                    self._ticker_tags = [t.strip().upper() for t in val if t.strip()]
                elif isinstance(val, str):
                    self._ticker_tags = [t.strip().upper()
                                         for t in val.split(",") if t.strip()]
                else:
                    self._ticker_tags = []
                self._fixed_tickers_entry.configure(state="normal")
                self._fixed_tickers_entry.delete(0, "end")
                if not self._settings_edit_mode:
                    self._fixed_tickers_entry.configure(state="disabled")
                self._rebuild_ticker_tags()
            elif isinstance(field_data, tuple):
                if len(field_data) == 4:
                    var, _, _, _ = field_data
                    # Display large numbers in human-readable format
                    try:
                        num = float(val)
                        if abs(num) >= 1000:
                            var.set(format_human_number(num))
                        else:
                            var.set(str(val))
                    except (ValueError, TypeError):
                        var.set(str(val))
                elif len(field_data) == 2:
                    var, widget = field_data
                    if isinstance(widget, ctk.CTkSwitch):
                        var.set("on" if val else "off")
                    elif key.startswith("ai_model_"):
                        # Translate API ID → display name
                        display = self._ai_model_reverse.get(str(val), str(val))
                        var.set(display)
                    else:
                        var.set(str(val))

        self._set_sync_indicator(True)

    # ── Settings tab: save ──

    def _save_settings(self) -> None:
        """Collect values, validate locally, send to server."""
        # Critical change guard: trading mode paper→live
        old_mode = self._mode_switch_var.get()
        new_mode = self._mode_switch_var.get()
        # We check what's in the current widget vs what server had
        # For now, if user tries to go live, confirm
        if new_mode == "live":
            dialog = ctk.CTkInputDialog(
                text="Switching to LIVE mode trades REAL money!\n"
                     "Type 'CONFIRM' to proceed.",
                title="⚠  Master Lock — Trading Mode",
            )
            result = dialog.get_input()
            if result != "CONFIRM":
                return

        patch = self._collect_settings_patch()
        if patch is None:
            return  # validation failed

        self._settings_save_btn.configure(state="disabled", text="Saving…")

        def _work() -> None:
            ok = False
            errors: list[str] = []
            try:
                resp = self._http.put("/api/settings",
                                      json={"patch": patch}, timeout=10)
                if resp.status_code == 200:
                    body = resp.json()
                    ok = body.get("ok", False)
                    errors = body.get("errors", [])
                    if ok:
                        self.after(0, self._apply_settings_to_ui,
                                  body.get("settings", {}))
            except Exception as exc:
                errors = [str(exc)]

            def _done() -> None:
                self._settings_save_btn.configure(state="normal",
                                                   text="💾 Save & Apply")
                if ok:
                    self._set_sync_indicator(True)
                    self._append_log({
                        "agent": "system", "action": "settings_updated",
                        "thought": "Settings saved and applied.",
                    })
                else:
                    self._set_sync_indicator(False)
                    err_text = "; ".join(errors) if errors else "Unknown error"
                    self._append_log({
                        "agent": "system", "action": "settings_error",
                        "thought": f"Settings save failed: {err_text}",
                    })

            self.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def _collect_settings_patch(self) -> dict[str, Any] | None:
        """Read all widgets and build a patch dict. Returns None on error."""
        patch: dict[str, Any] = {}
        errors: list[str] = []

        for key, field_data in self._settings_fields.items():
            if key == "trading_mode":
                patch[key] = self._mode_switch_var.get()
            elif key == "enable_dynamic_universe":
                patch[key] = self._dyn_universe_var.get() == "on"
            elif key == "display_timezone":
                patch[key] = self._tz_var.get()
            elif key == "fixed_tickers":
                # Read from tag list (not the entry text)
                patch[key] = list(self._ticker_tags)
            elif isinstance(field_data, tuple):
                if len(field_data) == 4:
                    var, _, lo, hi = field_data
                    raw_val = var.get().strip()
                    # Support K/M/B/T suffixes (e.g. "10B", "5.5M")
                    num = parse_human_number(raw_val)
                    if num is None:
                        errors.append(f"{key}: not a valid number")
                        continue
                    if num < lo or num > hi:
                        errors.append(f"{key}: must be {lo}–{hi}")
                        continue
                    # Use int if the default is int-like
                    patch[key] = int(num) if num == int(num) and lo >= 1 else num
                elif len(field_data) == 2:
                    var, widget = field_data
                    if isinstance(widget, ctk.CTkSwitch):
                        patch[key] = var.get() == "on"
                    elif key.startswith("ai_model_"):
                        # Translate display name → API ID
                        display = var.get()
                        patch[key] = self._ai_model_map.get(display, display)
                    else:
                        patch[key] = var.get()

        if errors:
            err_text = "\n".join(errors)
            self._append_log({
                "agent": "system", "action": "validation_error",
                "thought": f"Settings validation failed:\n{err_text}",
            })
            return None
        return patch

    # ── Settings tab: reset ──

    def _reset_settings(self) -> None:
        dialog = ctk.CTkInputDialog(
            text="This will reset ALL settings to factory defaults.\n"
                 "Type 'CONFIRM' to proceed.",
            title="↺  Reset to Default",
        )
        result = dialog.get_input()
        if result != "CONFIRM":
            return

        def _work() -> None:
            try:
                resp = self._http.post("/api/settings/reset", timeout=10)
                if resp.status_code == 200:
                    body = resp.json()
                    if body.get("ok"):
                        self.after(0, self._apply_settings_to_ui,
                                  body.get("settings", {}))
                        self.after(0, self._set_sync_indicator, True)
                        self.after(0, self._append_log, {
                            "agent": "system", "action": "settings_reset",
                            "thought": "Settings reset to defaults.",
                        })
                        return
            except Exception:
                pass
            self.after(0, self._set_sync_indicator, False)

        threading.Thread(target=_work, daemon=True).start()

    # ── Settings tab: API keys ──

    def _submit_api_keys(self) -> None:
        keys: dict[str, str] = {}
        for key_name, entry in self._api_key_entries.items():
            val = entry.get().strip()
            if val:
                keys[key_name] = val

        if not keys:
            return

        # Wipe from UI immediately
        for entry in self._api_key_entries.values():
            entry.configure(state="normal")
            entry.delete(0, "end")
            if not self._settings_edit_mode:
                entry.configure(state="disabled")

        self._submit_keys_btn.configure(state="disabled", text="Sending…")

        def _work() -> None:
            ok = False
            try:
                resp = self._http.post("/api/keys", json=keys, timeout=10)
                ok = resp.status_code == 200 and resp.json().get("ok", False)
            except Exception:
                pass

            def _done() -> None:
                self._submit_keys_btn.configure(
                    state="normal" if self._settings_edit_mode else "disabled",
                    text="🔑 Apply Keys to Server",
                )
                if ok:
                    self._append_log({
                        "agent": "system", "action": "api_keys_updated",
                        "thought": f"API keys submitted: {', '.join(keys.keys())}",
                    })
                    self._load_key_status()
                else:
                    self._append_log({
                        "agent": "system", "action": "error",
                        "thought": "Failed to submit API keys.",
                    })

            self.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def _load_key_status(self) -> None:
        def _fetch() -> None:
            try:
                resp = self._http.get("/api/keys/status")
                if resp.status_code == 200:
                    data = resp.json()
                    self.after(0, self._apply_key_status, data)
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _apply_key_status(self, data: dict) -> None:
        for key_name, lbl in self._key_status_labels.items():
            status = data.get(key_name, "Unknown")
            color = ACCENT_GREEN if "Loaded" in status else ACCENT_AMBER
            lbl.configure(text=status, text_color=color)

    # ── Sync indicator ──

    def _set_sync_indicator(self, synced: bool) -> None:
        if synced:
            self._sync_indicator.configure(
                text="●  Server Synced", text_color=ACCENT_GREEN)
        else:
            self._sync_indicator.configure(
                text="●  Out of Sync", text_color=ACCENT_RED)

    # ==================================================================
    # Chart helpers
    # ==================================================================
    def _style_ax(self) -> None:
        self._ax.set_facecolor(BG_PRIMARY)
        self._ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
        for spine in self._ax.spines.values():
            spine.set_color(BORDER_SUBTLE)
        self._ax.set_xlabel("Ticks", color=TEXT_SECONDARY, fontsize=9)
        self._ax.set_ylabel("Price ($)", color=TEXT_SECONDARY, fontsize=9)
        self._ax.grid(True, color=BORDER_SUBTLE, alpha=0.3, linewidth=0.5)

    # ------------------------------------------------------------------
    # Ticker Search (autocomplete)
    # ------------------------------------------------------------------
    def _on_ticker_search(self, event=None) -> None:
        """Fire a search request on each keystroke."""
        query = self._ticker_search_var.get().strip()
        if len(query) < 1:
            self._hide_ticker_dropdown()
            return

        def _fetch():
            try:
                resp = self._http.get(
                    "/api/tickers/search",
                    params={"q": query, "limit": 10},
                )
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    self.after(0, self._show_ticker_dropdown, results)
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _show_ticker_dropdown(self, results: list[str]) -> None:
        """Display search results in a floating dropdown."""
        # Clear old buttons
        for btn in self._ticker_dropdown_buttons:
            btn.destroy()
        self._ticker_dropdown_buttons.clear()

        if not results:
            self._hide_ticker_dropdown()
            return

        for sym in results:
            btn = ctk.CTkButton(
                self._ticker_dropdown, text=sym, height=24, width=120,
                font=FONT_MONO_SM, corner_radius=0, anchor="w",
                fg_color=BG_WIDGET, hover_color=BG_HOVER,
                text_color=TEXT_PRIMARY,
                command=lambda s=sym: self._select_search_ticker(s),
            )
            btn.pack(fill="x")
            self._ticker_dropdown_buttons.append(btn)

        # Position dropdown below the search entry
        entry = self._ticker_search_entry
        x = entry.winfo_rootx() - self._ticker_dropdown.master.winfo_rootx()
        y = (entry.winfo_rooty() - self._ticker_dropdown.master.winfo_rooty()
             + entry.winfo_height())
        self._ticker_dropdown.place(x=x, y=y, width=140)
        self._ticker_dropdown.lift()

    def _hide_ticker_dropdown(self) -> None:
        """Hide the autocomplete dropdown."""
        self._ticker_dropdown.place_forget()
        for btn in self._ticker_dropdown_buttons:
            btn.destroy()
        self._ticker_dropdown_buttons.clear()

    def _select_search_ticker(self, symbol: str) -> None:
        """User clicked a search result — switch chart to that ticker."""
        self._ticker_search_var.set("")
        self._hide_ticker_dropdown()
        self._selected_chart_symbol = symbol
        self._fetch_candles(symbol, self._selected_timeframe)
        self._highlight_universe_button(symbol)

    def _on_ticker_search_select(self, event=None) -> None:
        """Enter pressed — pick the first dropdown item or search text."""
        if self._ticker_dropdown_buttons:
            first = self._ticker_dropdown_buttons[0].cget("text")
            self._select_search_ticker(first)
        else:
            raw = self._ticker_search_var.get().strip().upper()
            if raw:
                self._select_search_ticker(raw)

    def _on_timeframe_change(self, tf: str) -> None:
        """Handle timeframe button click."""
        self._selected_timeframe = tf
        for t, btn in self._tf_buttons.items():
            if t == tf:
                btn.configure(fg_color="#2b3a42", text_color=ACCENT_CYAN)
            else:
                btn.configure(fg_color=BG_INPUT, text_color=TEXT_SECONDARY)

        # Fetch candles for the selected symbol + timeframe
        if self._selected_chart_symbol:
            self._fetch_candles(self._selected_chart_symbol, tf)

    def _fetch_candles(self, symbol: str, timeframe: str) -> None:
        """Fetch candle data from server in a background thread."""
        def _fetch():
            try:
                resp = self._http.get(
                    "/api/candles",
                    params={"symbol": symbol, "timeframe": timeframe, "limit": 100},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    candles = data.get("candles", [])
                    if candles:
                        self.after(0, self._draw_candle_chart, symbol, candles)
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _draw_candle_chart(self, symbol: str, candles: list[dict]) -> None:
        """Draw candle data on the chart via the throttled blit system."""
        prices = [c.get("close", 0) for c in candles]
        if not prices:
            return
        self._chart_data = {symbol: prices}
        self._chart_lines.clear()  # force full redraw
        self._chart_dirty = True
        self._do_chart_redraw()

    # ==================================================================
    # Ticker Cards
    # ==================================================================
    def _update_ticker_cards(self, cards: list[dict]) -> None:
        """Update the ticker cards in the sidebar."""
        # Clear existing cards
        for widget in self._ticker_cards_frame.winfo_children():
            widget.destroy()
        self._ticker_card_labels.clear()

        for card_data in cards[:8]:  # max 8 cards
            sym = card_data.get("symbol", "")
            price = card_data.get("price", 0)
            change = card_data.get("change_pct", 0)
            signal = card_data.get("signal", "NEUTRAL")
            confidence = card_data.get("confidence", 0)

            row = ctk.CTkFrame(self._ticker_cards_frame,
                               fg_color=BG_INPUT, corner_radius=6)
            row.pack(fill="x", pady=2)
            row.grid_columnconfigure(1, weight=1)

            # Signal colour
            sig_colors = {
                "BULLISH": ACCENT_GREEN,
                "BEARISH": ACCENT_RED,
                "NEUTRAL": TEXT_SECONDARY,
            }
            sig_color = sig_colors.get(signal, TEXT_SECONDARY)

            # Signal dot
            _label(row, text="●", font=("Pretendard", 10),
                   text_color=sig_color).grid(row=0, column=0, padx=(8, 4), pady=4)

            # Symbol
            sym_lbl = _label(row, text=sym, font=("JetBrains Mono", 11, "bold"),
                            text_color=TEXT_HEADING)
            sym_lbl.grid(row=0, column=1, sticky="w", pady=4)

            # Price + change
            chg_color = ACCENT_GREEN if change >= 0 else ACCENT_RED
            price_text = f"${price:,.2f}  {change:+.1f}%"
            _label(row, text=price_text, font=FONT_MONO_SM,
                   text_color=chg_color).grid(row=0, column=2, padx=8, pady=4)

            # Click to select for chart
            row.bind("<Button-1>", lambda e, s=sym: self._on_ticker_click(s))
            for child in row.winfo_children():
                child.bind("<Button-1>", lambda e, s=sym: self._on_ticker_click(s))

        self.after(100, self._check_sidebar_scroll)

    def _on_ticker_click(self, symbol: str) -> None:
        """Select a ticker for the chart."""
        self._selected_chart_symbol = symbol
        self._fetch_candles(symbol, self._selected_timeframe)
        self._highlight_universe_button(symbol)

    def _update_universe_display(self, symbols: list[str]) -> None:
        """Delta-update the Universe section — only add/remove changed tickers."""
        if symbols == self._universe_symbols:
            return  # identical list, skip re-render entirely

        old_set = set(self._universe_symbols)
        new_set = set(symbols)

        # Remove tickers no longer in universe
        for sym in old_set - new_set:
            btn = self._universe_buttons.pop(sym, None)
            if btn:
                btn.destroy()

        # If first render, clear the "Loading…" placeholder
        if not self._universe_symbols and self._universe_inner.winfo_children():
            for w in self._universe_inner.winfo_children():
                if isinstance(w, ctk.CTkLabel):
                    w.destroy()

        # Add new tickers
        for sym in symbols:
            if sym not in self._universe_buttons:
                is_selected = sym == self._selected_chart_symbol
                btn = ctk.CTkButton(
                    self._universe_inner, text=sym, width=64, height=26,
                    font=FONT_MONO_SM, corner_radius=4,
                    fg_color="#2b3a42" if is_selected else BG_INPUT,
                    text_color=ACCENT_CYAN if is_selected else TEXT_SECONDARY,
                    hover_color="#2b3a42",
                    command=lambda s=sym: self._on_ticker_click(s),
                )
                btn.pack(side="left", padx=2, pady=2)
                self._universe_buttons[sym] = btn

        self._universe_symbols = list(symbols)

        if symbols and not self._selected_chart_symbol:
            self._selected_chart_symbol = symbols[0]
            self._highlight_universe_button(symbols[0])

        # No empty state
        if not symbols:
            _label(self._universe_inner, text="—", font=FONT_MONO_SM,
                   text_color=TEXT_SECONDARY).pack(side="left")

    def _highlight_universe_button(self, symbol: str) -> None:
        """Highlight the selected universe ticker button."""
        for sym, btn in self._universe_buttons.items():
            if sym == symbol:
                btn.configure(fg_color="#2b3a42", text_color=ACCENT_CYAN)
            else:
                btn.configure(fg_color=BG_INPUT, text_color=TEXT_SECONDARY)

    # ==================================================================
    # SSE Integration
    # ==================================================================
    def _start_sse(self) -> None:
        self._sse = SSEReader(SSE_URL, self._on_sse_event)
        self._sse.start()

    def _on_sse_event(self, event_type: str, data: dict) -> None:
        """Schedule SSE event processing with throttling (100-200 ms)."""
        self.after(0, self._buffer_sse_event, event_type, data)

    def _buffer_sse_event(self, event_type: str, data: dict) -> None:
        """Buffer SSE events and schedule a throttled flush."""
        self._pending_sse_updates.append((event_type, data))
        if not self._sse_flush_scheduled:
            self._sse_flush_scheduled = True
            self.after(self._ui_throttle_ms, self._flush_sse_updates)

    def _flush_sse_updates(self) -> None:
        """Process all buffered SSE events in one batch."""
        self._sse_flush_scheduled = False
        events = self._pending_sse_updates[:]
        self._pending_sse_updates.clear()
        for event_type, data in events:
            self._process_sse(event_type, data)

    def _process_sse(self, event_type: str, data: dict) -> None:
        try:
            if event_type == "market_data":
                self._update_chart(data)
            elif event_type == "insight":
                self._append_feed(data)
            elif event_type == "strategy":
                self._update_strategy(data)
            elif event_type == "review":
                self._append_review(data)
            elif event_type == "portfolio":
                self._update_portfolio_display(data)
            elif event_type == "log":
                self._append_feed(data)
                self._append_log(data)
            elif event_type == "universe":
                symbols = data.get("symbols", [])
                self._update_universe_display(symbols)
            elif event_type == "ai_thinking":
                self._handle_ai_thinking(data)
        except Exception:
            pass

    # ==================================================================
    # Polling fallback (background-threaded)
    # ==================================================================
    def _poll_status(self) -> None:
        """Fetch status on a background thread; apply on the main thread."""
        def _fetch() -> None:
            try:
                resp = self._http.get("/api/status")
                if resp.status_code == 200:
                    data = resp.json()
                    self.after(0, self._apply_status, data)
                else:
                    self.after(0, self._set_indicator, "server", False)
            except Exception:
                self.after(0, self._set_indicator, "server", False)

        threading.Thread(target=_fetch, daemon=True).start()
        self.after(POLL_INTERVAL_MS, self._poll_status)

    def _apply_status(self, data: dict) -> None:
        """Apply polled status data to the UI (main thread only)."""
        self._set_indicator("server", True)
        self._bot_running = data.get("bot_status") == "running"
        self._update_bot_buttons()

        if data.get("portfolio"):
            self._update_portfolio_display(data["portfolio"])

        symbols = data.get("tracked_symbols", [])
        self._update_universe_display(symbols)

        mode = "PAPER" if "paper" in data.get("trading_mode", "") else "LIVE"
        if mode == "PAPER":
            self._mode_badge.configure(
                text="  PAPER  ", fg_color="#3b3416",
                text_color=ACCENT_GOLD)
        else:
            self._mode_badge.configure(
                text="  LIVE  ", fg_color="#3b1219",
                text_color=ACCENT_RED)

        strategy = data.get("current_strategy")
        if strategy:
            self._update_strategy({"reasoning": strategy})

        risk = data.get("risk", {})
        if risk.get("is_emergency"):
            self._mode_badge.configure(
                text="  ⚠ EMERGENCY  ", fg_color="#3b1219",
                text_color=ACCENT_RED)

        if data.get("is_rest_mode"):
            self._rest_badge.configure(
                text="  💤 REST  ", fg_color="#3b3416")
        else:
            self._rest_badge.configure(text="", fg_color=BG_WIDGET)

        cost = data.get("ai_cost_today", 0)
        self._cost_badge.configure(text=f"  AI: ${cost:.2f}  ")

        cards = data.get("ticker_cards", [])
        if cards:
            self._update_ticker_cards(cards)

    # ==================================================================
    # LED indicator helpers
    # ==================================================================
    def _set_indicator(self, key: str, connected: bool) -> None:
        if key not in self._status_indicators:
            return
        dot, lbl = self._status_indicators[key]
        if connected:
            dot.configure(text_color=ACCENT_GREEN)
            lbl.configure(text="connected", text_color=ACCENT_GREEN)
        else:
            dot.configure(text_color=ACCENT_RED)
            lbl.configure(text="offline", text_color=ACCENT_RED)

    def _set_indicator_amber(self, key: str, msg: str = "partial") -> None:
        if key not in self._status_indicators:
            return
        dot, lbl = self._status_indicators[key]
        dot.configure(text_color=ACCENT_AMBER)
        lbl.configure(text=msg, text_color=ACCENT_AMBER)

    # ==================================================================
    # Pulsing animation (bot running)
    # ==================================================================
    def _pulse_tick(self) -> None:
        if not self._bot_running:
            self._pulse_dot.configure(text_color=BG_WIDGET)
            return
        self._pulse_phase = (self._pulse_phase + 1) % 20
        if self._pulse_phase < 10:
            self._pulse_dot.configure(text_color=ACCENT_GREEN)
        else:
            self._pulse_dot.configure(text_color=BG_HOVER)
        self.after(100, self._pulse_tick)

    # ==================================================================
    # UI Update Helpers
    # ==================================================================
    def _update_bot_buttons(self) -> None:
        if self._bot_running:
            self._start_btn.configure(state="disabled", fg_color=BG_HOVER,
                                      text_color=TEXT_SECONDARY)
            self._stop_btn.configure(state="normal", fg_color="#3b2a10",
                                     hover_color="#5c3d15",
                                     text_color=ACCENT_GOLD)
            self._pulse_tick()
        else:
            self._start_btn.configure(state="normal", fg_color="#1b4332",
                                      text_color=ACCENT_GREEN)
            self._stop_btn.configure(state="disabled", fg_color=BG_HOVER,
                                     text_color=TEXT_SECONDARY)
            self._pulse_dot.configure(text_color=BG_WIDGET)

    def _update_portfolio_display(self, data: dict) -> None:
        equity = data.get("equity", 0)
        cash = data.get("cash", 0)
        daily = data.get("daily_pl", 0)
        daily_pct = data.get("daily_pl_pct", 0)
        total = data.get("total_pl", 0)
        total_pct = data.get("total_pl_pct", 0)
        positions = data.get("positions_count", data.get("positions", 0))
        if isinstance(positions, list):
            positions = len(positions)

        self._equity_label.configure(text=f"${equity:,.2f}")
        self._portfolio_vals["cash"].configure(text=f"${cash:,.2f}")

        d_color = ACCENT_GREEN if daily >= 0 else ACCENT_RED
        self._portfolio_vals["daily_pl"].configure(
            text=f"${daily:+,.2f} ({daily_pct:+.1f}%)", text_color=d_color)

        t_color = ACCENT_GREEN if total >= 0 else ACCENT_RED
        self._portfolio_vals["total_pl"].configure(
            text=f"${total:+,.2f} ({total_pct:+.1f}%)", text_color=t_color)

        self._portfolio_vals["positions"].configure(text=str(positions))

    def _update_chart(self, data: dict) -> None:
        symbol = data.get("symbol", "")
        price = data.get("price", 0)
        if not symbol or price <= 0:
            return

        if not self._selected_chart_symbol:
            self._selected_chart_symbol = symbol

        if symbol not in self._chart_data:
            self._chart_data[symbol] = []
        self._chart_data[symbol].append(price)
        if len(self._chart_data[symbol]) > 100:
            self._chart_data[symbol] = self._chart_data[symbol][-100:]

        self._chart_dirty = True
        self._schedule_chart_redraw()

    def _schedule_chart_redraw(self) -> None:
        """Throttle chart redraws to at most once per _chart_throttle_s."""
        if self._chart_redraw_pending:
            return
        now = time.monotonic()
        elapsed = now - self._last_chart_draw
        if elapsed >= self._chart_throttle_s:
            self._do_chart_redraw()
        else:
            self._chart_redraw_pending = True
            delay = int((self._chart_throttle_s - elapsed) * 1000)
            self.after(max(delay, 50), self._do_chart_redraw)

    def _do_chart_redraw(self) -> None:
        """Efficient chart redraw — full redraw only when symbol set changes,
        otherwise update existing Line2D data (blit-style)."""
        self._chart_redraw_pending = False
        if not self._chart_dirty:
            return
        self._chart_dirty = False
        self._last_chart_draw = time.monotonic()

        if not self._chart_data:
            return

        palette = [ACCENT_CYAN, ACCENT_GOLD, ACCENT_GREEN, ACCENT_RED,
                   ACCENT_PURPLE]
        current_syms = set(self._chart_data.keys())
        cached_syms = set(self._chart_lines.keys())

        if current_syms != cached_syms:
            # Symbol set changed → full redraw
            self._ax.clear()
            self._style_ax()
            self._chart_lines.clear()
            for i, (sym, prices) in enumerate(self._chart_data.items()):
                c = palette[i % len(palette)]
                (line,) = self._ax.plot(
                    prices, label=sym, color=c, linewidth=1.6, alpha=0.9)
                self._chart_lines[sym] = line
            if self._chart_data:
                self._ax.legend(
                    facecolor=BG_WIDGET, labelcolor=TEXT_PRIMARY,
                    fontsize=8, edgecolor=BORDER_SUBTLE, framealpha=0.9)
            self._fig.tight_layout(pad=1.5)
            self._canvas.draw()
        else:
            # Incremental update — only update line data
            for sym, prices in self._chart_data.items():
                line = self._chart_lines[sym]
                line.set_data(range(len(prices)), prices)
            self._ax.relim()
            self._ax.autoscale_view()
            self._canvas.draw_idle()

    def _append_feed(self, data: dict) -> None:
        """Append insight or log to the data feed with colour tags."""
        # End any active streaming block before a normal feed entry
        if self._ai_streaming_agent:
            self._ai_streaming_agent = None
            self._feed_text.configure(state="normal")
            self._feed_text._textbox.insert("end", "\n")
            self._feed_text.configure(state="disabled")

        self._feed_text.configure(state="normal")
        tw = self._feed_text._textbox

        timestamp = data.get("timestamp",
                             datetime.now(timezone.utc).isoformat())[:19]
        agent = data.get("provider", data.get("agent", "system")).lower()
        summary = data.get("summary", data.get("thought", ""))
        category = data.get("category", data.get("action", ""))
        symbol = data.get("symbol", "")

        # Determine tag
        if "error" in (category or "").lower():
            tag = "error"
        elif agent in ("grok_fast", "grok-fast"):
            tag = "grok_fast"
        elif agent in ("grok", "grok_strategy"):
            tag = "grok"
        elif agent in ("opus", "claude"):
            tag = "opus"
        else:
            tag = "system"

        sym_str = f" [{symbol}]" if symbol else ""
        cat_str = f" ({category})" if category else ""

        tw.insert("end", f"[{timestamp}]", "ts")
        tw.insert("end", f" [{agent.upper()}]{sym_str}{cat_str}\n", tag)
        tw.insert("end", f"{summary}\n\n", tag)
        tw.see("end")
        self._feed_text.configure(state="disabled")

    def _handle_ai_thinking(self, data: dict) -> None:
        """Handle real-time AI thinking/streaming SSE events."""
        action = data.get("action", "")
        agent = data.get("agent", "system").lower()
        thought = data.get("thought", "")

        tw = self._feed_text._textbox

        if action == "thinking":
            # Start of a new thinking block — show indicator
            self._ai_streaming_agent = agent
            self._feed_text.configure(state="normal")
            timestamp = data.get("timestamp", "")[:19]
            tag = ("grok_fast" if "grok_fast" in agent
                   else "grok" if "grok" in agent
                   else "opus" if "opus" in agent or "claude" in agent
                   else "system")
            tw.insert("end", f"[{timestamp}]", "ts")
            tw.insert("end", f" [{agent.upper()}] ", tag)
            tw.insert("end", f"{thought}\n", "thinking")
            tw.see("end")
            self._feed_text.configure(state="disabled")

        elif action == "streaming":
            # Append streaming tokens inline
            self._feed_text.configure(state="normal")
            tw.insert("end", thought, "streaming")
            tw.see("end")
            self._feed_text.configure(state="disabled")

        elif action == "done_thinking":
            # End streaming block with a newline
            self._ai_streaming_agent = None
            self._feed_text.configure(state="normal")
            tw.insert("end", "\n\n")
            tw.see("end")
            self._feed_text.configure(state="disabled")

        else:
            # Other thought actions (tool_call, etc.) — show as feed line
            self._append_feed({
                "agent": agent,
                "thought": thought,
                "action": action,
                "timestamp": data.get("timestamp", ""),
            })

    def _append_log(self, data: dict) -> None:
        """Append to the Full Logs tab with syntax highlighting."""
        self._log_text.configure(state="normal")
        tw = self._log_text._textbox

        timestamp = data.get("timestamp",
                             datetime.now(timezone.utc).isoformat())[:19]
        agent = data.get("provider", data.get("agent", "system")).lower()
        action = data.get("action", data.get("category", ""))
        thought = data.get("thought", data.get("summary", ""))

        if "error" in (action or "").lower():
            tag = "error"
        elif "strategy" in (action or "").lower():
            tag = "strategy"
        elif "warn" in (action or "").lower():
            tag = "warn"
        else:
            tag = "info"

        tw.insert("end", f"[{timestamp}] ", "ts")
        tw.insert("end", f"[{agent.upper()}] ", tag)
        if action:
            tw.insert("end", f"({action}) ", "dim")
        tw.insert("end", f"{thought}\n", tag)
        tw.see("end")

        self._log_entry_count += 1
        self._log_count_label.configure(text=f"{self._log_entry_count} entries")
        self._log_text.configure(state="disabled")

    def _update_strategy(self, data: dict) -> None:
        self._strategy_text.configure(state="normal")
        tw = self._strategy_text._textbox
        tw.delete("1.0", "end")

        version = data.get("version", "—")
        confidence = data.get("confidence", "—")
        reasoning = data.get("reasoning", "No strategy available.")
        risk = data.get("risk_notes", "")
        cost = data.get("cost_usd", 0)
        accepted = data.get("hypothesis_accepted", 0)
        rejected = data.get("hypothesis_rejected", 0)

        self._strategy_badge.configure(text=f"  v{version}  ")

        # Update confidence gauge
        if isinstance(confidence, (int, float)):
            conf_pct = int(confidence * 100)
            if confidence >= 0.8:
                conf_color = ACCENT_GREEN
            elif confidence >= 0.5:
                conf_color = ACCENT_AMBER
            else:
                conf_color = ACCENT_RED
            self._confidence_label.configure(
                text=f"Confidence: {conf_pct}%", text_color=conf_color)
        else:
            self._confidence_label.configure(
                text="Confidence: —", text_color=TEXT_SECONDARY)

        tw.insert("end",
                   f"═══ Strategy v{version}  ·  confidence: {confidence} ═══\n\n",
                   "heading")
        tw.insert("end", f"{reasoning}\n\n")

        # Hypothesis stats
        if accepted or rejected:
            tw.insert("end", f"Hypotheses accepted: {accepted}\n", "accepted")
            tw.insert("end", f"Hypotheses rejected: {rejected}\n\n", "rejected")

        if risk:
            tw.insert("end", "── Risk Notes ──\n", "risk")
            tw.insert("end", f"{risk}\n\n", "risk")

        if cost:
            tw.insert("end", f"[Opus cost: ${cost:.4f}]\n", "cost")

        self._strategy_text.configure(state="disabled")

    def _append_review(self, data: dict) -> None:
        self._review_text.configure(state="normal")
        tw = self._review_text._textbox

        version = data.get("strategy_version", "?")
        correction = data.get("self_correction", "")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        tw.insert("end", f"[{ts}] ", "ts")
        tw.insert("end", f"Strategy v{version} Review\n", "version")
        tw.insert("end", f"{correction}\n")
        tw.insert("end", f"{'─' * 60}\n\n", "sep")
        tw.see("end")
        self._review_text.configure(state="disabled")

    # ==================================================================
    # Chat
    # ==================================================================
    def _on_chat_send(self) -> None:
        message = self._chat_input.get().strip()
        if not message:
            return

        self._chat_input.delete(0, "end")
        self._append_chat_message("You", message, is_user=True)
        self._chat_send_btn.configure(state="disabled", text="…")

        def _send():
            try:
                resp = self._http.post("/api/chat", json={"message": message})
                if resp.status_code == 200:
                    response = resp.json().get("response", "No response.")
                    self.after(0, self._append_chat_message,
                              "Opus CEO", response, False)
                else:
                    self.after(0, self._append_chat_message,
                              "System", "Failed to get response.", False)
            except Exception as e:
                self.after(0, self._append_chat_message,
                          "System", f"Error: {e}", False)
            finally:
                self.after(0, lambda: self._chat_send_btn.configure(
                    state="normal", text="Send"))

        threading.Thread(target=_send, daemon=True).start()

    def _append_chat_message(self, sender: str, message: str,
                              is_user: bool = True) -> None:
        self._chat_display.configure(state="normal")
        tw = self._chat_display._textbox

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        label_tag = "user_label" if is_user else "opus_label"
        msg_tag = "user_msg" if is_user else "opus_msg"

        tw.insert("end", f"[{ts}] ", "ts")
        tw.insert("end", f"{sender}\n", label_tag)
        tw.insert("end", f"{message}\n\n", msg_tag)
        tw.see("end")
        self._chat_display.configure(state="disabled")

    # ==================================================================
    # Journal
    # ==================================================================
    def _refresh_journal(self) -> None:
        def _fetch():
            try:
                resp = self._http.get("/api/journal", params={"limit": 30})
                if resp.status_code == 200:
                    entries = resp.json()
                    self.after(0, self._render_journal, entries)
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _render_journal(self, entries: list[dict]) -> None:
        self._journal_text.configure(state="normal")
        tw = self._journal_text._textbox
        tw.delete("1.0", "end")

        if not entries:
            tw.insert("end", "No journal entries yet.\n\n"
                      "Journal entries are generated automatically at the "
                      "end of each trading day.", "system")
            self._journal_text.configure(state="disabled")
            return

        for entry in entries:
            date = entry.get("date", "—")
            summary = entry.get("summary", "")
            trades = entry.get("trades_count", 0)
            wins = entry.get("wins", 0)
            losses = entry.get("losses", 0)
            pnl = entry.get("pnl", 0)
            lessons = entry.get("lessons", [])
            conditions = entry.get("market_conditions", "")

            tw.insert("end", f"📅 {date}\n", "date")
            tw.insert("end", f"{summary}\n")
            tw.insert("end", f"  Trades: {trades}  |  ", "dim")
            tw.insert("end", f"Wins: {wins}  ", "win")
            tw.insert("end", f"Losses: {losses}  |  ", "loss")

            pnl_tag = "win" if pnl >= 0 else "loss"
            tw.insert("end", f"P&L: ${pnl:+,.2f}\n", pnl_tag)

            if conditions:
                tw.insert("end", f"  Market: {conditions}\n")

            if lessons:
                tw.insert("end", "  Lessons:\n", "lesson")
                for lesson in lessons:
                    tw.insert("end", f"    • {lesson}\n", "lesson")

            tw.insert("end", f"{'─' * 60}\n\n", "sep")

        self._journal_text.configure(state="disabled")

    # ==================================================================
    # Button Handlers (HTTP calls run on background threads)
    # ==================================================================
    def _on_start(self) -> None:
        self._start_btn.configure(state="disabled")

        def _work() -> None:
            success = False
            try:
                resp = self._http.post("/api/bot/start")
                success = resp.status_code == 200
            except Exception:
                pass

            def _update() -> None:
                if success:
                    self._bot_running = True
                    self._update_bot_buttons()
                    self._append_feed({
                        "agent": "system", "action": "bot_started",
                        "thought": "Bot started successfully.",
                    })
                    self._append_log({
                        "agent": "system", "action": "bot_started",
                        "thought": "Bot started successfully.",
                    })
                else:
                    self._start_btn.configure(state="normal")
                    self._append_feed({
                        "agent": "system", "action": "error",
                        "thought": "Failed to start bot.",
                    })

            self.after(0, _update)

        threading.Thread(target=_work, daemon=True).start()

    def _on_stop(self) -> None:
        self._stop_btn.configure(state="disabled")

        def _work() -> None:
            success = False
            try:
                resp = self._http.post("/api/bot/stop")
                success = resp.status_code == 200
            except Exception:
                pass

            def _update() -> None:
                if success:
                    self._bot_running = False
                    self._update_bot_buttons()
                    self._append_feed({
                        "agent": "system", "action": "bot_stopped",
                        "thought": "Bot stopped.",
                    })
                    self._append_log({
                        "agent": "system", "action": "bot_stopped",
                        "thought": "Bot stopped.",
                    })
                else:
                    self._stop_btn.configure(state="normal")
                    self._append_feed({
                        "agent": "system", "action": "error",
                        "thought": "Failed to stop bot.",
                    })

            self.after(0, _update)

        threading.Thread(target=_work, daemon=True).start()

    def _on_emergency(self) -> None:
        """Emergency kill switch — liquidate all and halt."""
        dialog = ctk.CTkInputDialog(
            text="Type 'CONFIRM' to execute emergency stop.\n"
                 "This will liquidate ALL positions immediately.",
            title="⚠  EMERGENCY KILL SWITCH",
        )
        result = dialog.get_input()
        if result != "CONFIRM":
            return

        self._kill_btn.configure(state="disabled")

        def _work() -> None:
            rdata = None
            liq = 0
            err_msg = ""
            try:
                resp = self._http.post("/api/bot/emergency")
                if resp.status_code == 200:
                    rdata = resp.json()
                    liq = len(rdata.get("liquidated", []))
            except Exception as e:
                err_msg = str(e)

            def _update() -> None:
                self._kill_btn.configure(state="normal")
                if rdata is not None:
                    self._bot_running = False
                    self._update_bot_buttons()
                    self._mode_badge.configure(
                        text="  ⚠ EMERGENCY  ", fg_color="#3b1219",
                        text_color=ACCENT_RED)
                    self._append_feed({
                        "agent": "system", "action": "emergency_stop",
                        "thought": f"Emergency stop executed. "
                                   f"Liquidated {liq} positions.",
                    })
                    self._append_log({
                        "agent": "system", "action": "emergency_stop",
                        "thought": f"Emergency stop. {liq} positions liquidated.",
                    })
                else:
                    self._append_feed({
                        "agent": "system", "action": "error",
                        "thought": f"Emergency stop failed: {err_msg}",
                    })

            self.after(0, _update)

        threading.Thread(target=_work, daemon=True).start()

    # ==================================================================
    # Cleanup
    # ==================================================================
    def destroy(self) -> None:
        if self._sse:
            self._sse.stop()
        try:
            self._http.close()
        except Exception:
            pass
        super().destroy()


# ===========================================================================
# Entry Point
# ===========================================================================
def main() -> None:
    app = TradingBotApp()
    app.mainloop()


if __name__ == "__main__":
    main()
