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

        self._build_ui()
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

        # Scrollable container
        scroll = ctk.CTkScrollableFrame(sidebar, fg_color="transparent", width=260)
        scroll.pack(fill="both", expand=True)

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
        self._symbols_label = _label(
            sym_card, text="Loading…", font=FONT_MONO_SM,
            text_color=ACCENT_CYAN, wraplength=250)
        self._symbols_label.pack(anchor="w", padx=PAD, pady=(0, PAD))

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

        self._build_tab_overview(tab_overview)
        self._build_tab_strategy(tab_strategy)
        self._build_tab_chat(tab_chat)
        self._build_tab_journal(tab_journal)
        self._build_tab_logs(tab_logs)

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

        # Multi-timeframe selector
        tf_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        tf_frame.pack(side="right")
        self._tf_buttons: dict[str, ctk.CTkButton] = {}
        for tf in ["1min", "5min", "15min", "1h", "1d", "1w"]:
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
        """Draw candle data on the chart."""
        prices = [c.get("close", 0) for c in candles]
        if not prices:
            return

        self._ax.clear()
        self._style_ax()
        self._ax.plot(prices, color=ACCENT_CYAN, linewidth=1.6, alpha=0.9,
                      label=f"{symbol} ({self._selected_timeframe})")
        self._ax.legend(
            facecolor=BG_WIDGET, labelcolor=TEXT_PRIMARY,
            fontsize=8, edgecolor=BORDER_SUBTLE, framealpha=0.9)
        self._fig.tight_layout(pad=1.5)
        self._canvas.draw_idle()

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

    def _on_ticker_click(self, symbol: str) -> None:
        """Select a ticker for the chart."""
        self._selected_chart_symbol = symbol
        self._fetch_candles(symbol, self._selected_timeframe)

    # ==================================================================
    # SSE Integration
    # ==================================================================
    def _start_sse(self) -> None:
        self._sse = SSEReader(SSE_URL, self._on_sse_event)
        self._sse.start()

    def _on_sse_event(self, event_type: str, data: dict) -> None:
        self.after(0, self._process_sse, event_type, data)

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
                self._symbols_label.configure(
                    text="  ·  ".join(symbols) if symbols else "—")
        except Exception:
            pass

    # ==================================================================
    # Polling fallback
    # ==================================================================
    def _poll_status(self) -> None:
        try:
            resp = self._http.get("/api/status")
            if resp.status_code == 200:
                data = resp.json()
                self._set_indicator("server", True)
                self._bot_running = data.get("bot_status") == "running"
                self._update_bot_buttons()

                if data.get("portfolio"):
                    self._update_portfolio_display(data["portfolio"])

                symbols = data.get("tracked_symbols", [])
                self._symbols_label.configure(
                    text="  ·  ".join(symbols) if symbols else "—")

                # Set first symbol as chart default
                if symbols and not self._selected_chart_symbol:
                    self._selected_chart_symbol = symbols[0]

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

                # Rest mode indicator
                if data.get("is_rest_mode"):
                    self._rest_badge.configure(
                        text="  💤 REST  ", fg_color="#3b3416")
                else:
                    self._rest_badge.configure(text="", fg_color=BG_WIDGET)

                # AI cost
                cost = data.get("ai_cost_today", 0)
                self._cost_badge.configure(text=f"  AI: ${cost:.2f}  ")

                # Ticker cards
                cards = data.get("ticker_cards", [])
                if cards:
                    self._update_ticker_cards(cards)
            else:
                self._set_indicator("server", False)
        except Exception:
            self._set_indicator("server", False)

        self.after(POLL_INTERVAL_MS, self._poll_status)

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

        # Set default chart symbol
        if not self._selected_chart_symbol:
            self._selected_chart_symbol = symbol

        if symbol not in self._chart_data:
            self._chart_data[symbol] = []
        self._chart_data[symbol].append(price)
        if len(self._chart_data[symbol]) > 100:
            self._chart_data[symbol] = self._chart_data[symbol][-100:]

        self._ax.clear()
        self._style_ax()

        palette = [ACCENT_CYAN, ACCENT_GOLD, ACCENT_GREEN, ACCENT_RED,
                   ACCENT_PURPLE]
        for i, (sym, prices) in enumerate(self._chart_data.items()):
            c = palette[i % len(palette)]
            self._ax.plot(prices, label=sym, color=c, linewidth=1.6, alpha=0.9)

        if self._chart_data:
            self._ax.legend(
                facecolor=BG_WIDGET, labelcolor=TEXT_PRIMARY,
                fontsize=8, edgecolor=BORDER_SUBTLE, framealpha=0.9)

        self._fig.tight_layout(pad=1.5)
        self._canvas.draw_idle()

    def _append_feed(self, data: dict) -> None:
        """Append insight or log to the data feed with colour tags."""
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
    # Button Handlers
    # ==================================================================
    def _on_start(self) -> None:
        try:
            resp = self._http.post("/api/bot/start")
            if resp.status_code == 200:
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
        except Exception as e:
            self._append_feed({
                "agent": "system", "action": "error",
                "thought": f"Failed to start bot: {e}",
            })

    def _on_stop(self) -> None:
        try:
            resp = self._http.post("/api/bot/stop")
            if resp.status_code == 200:
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
        except Exception as e:
            self._append_feed({
                "agent": "system", "action": "error",
                "thought": f"Failed to stop bot: {e}",
            })

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
        try:
            resp = self._http.post("/api/bot/emergency")
            if resp.status_code == 200:
                rdata = resp.json()
                self._bot_running = False
                self._update_bot_buttons()
                self._mode_badge.configure(
                    text="  ⚠ EMERGENCY  ", fg_color="#3b1219",
                    text_color=ACCENT_RED)
                liq = len(rdata.get("liquidated", []))
                self._append_feed({
                    "agent": "system", "action": "emergency_stop",
                    "thought": f"Emergency stop executed. "
                               f"Liquidated {liq} positions.",
                })
                self._append_log({
                    "agent": "system", "action": "emergency_stop",
                    "thought": f"Emergency stop. {liq} positions liquidated.",
                })
        except Exception as e:
            self._append_feed({
                "agent": "system", "action": "error",
                "thought": f"Emergency stop failed: {e}",
            })

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
