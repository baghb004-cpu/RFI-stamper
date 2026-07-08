"""Ground Truth section: verified on-site reality, distilled.  Animated KPI
counters, gauges and sparklines over the project store + the latest RFI scan,
plus a deterministic, rules-based insight feed (no cloud, no black box —
every insight names the rule that produced it)."""
from __future__ import annotations

import datetime as _dt
import os
import tkinter as tk
from tkinter import ttk

from . import fx
from .theme import mix, section_color

INSIGHT_GLYPH = {"risk": "▲", "watch": "◆", "good": "●"}
INSIGHT_COLOR = {"risk": "#d64545", "watch": "#d99c20", "good": "#2f9e62"}


class KpiTile(tk.Frame):
    def __init__(self, parent, theme, caption, color):
        super().__init__(parent, highlightthickness=1)
        self.theme = theme
        self.value_lbl = tk.Label(self, text="0", font=("Segoe UI", 24, "bold"))
        self.value_lbl.pack(anchor="w", padx=14, pady=(10, 0))
        self.cap_lbl = tk.Label(self, text=caption.upper(),
                                font=("Segoe UI", 8, "bold"))
        self.cap_lbl.pack(anchor="w", padx=14, pady=(0, 8))
        self.spark = fx.Sparkline(self, theme, width=150, height=26,
                                  color=color)
        self.spark.pack(anchor="w", padx=12, pady=(0, 10))
        self.counter = fx.CountUp(self.value_lbl)
        theme.register(lambda c: (
            self.configure(bg=c["card"], highlightbackground=c["border"]),
            self.value_lbl.configure(bg=c["card"], fg=color),
            self.cap_lbl.configure(bg=c["card"], fg=c["muted"])))

    def set(self, value, history=None):
        self.counter.to(float(value))
        if history:
            self.spark.set_data(history)


class TruthSection(ttk.Frame):
    def __init__(self, parent, theme, status, get_project, project_sec):
        super().__init__(parent)
        self.theme = theme
        self.get_project = get_project
        self.project_sec = project_sec
        col = section_color("truth")
        self.header = fx.GradientHeader(
            self, theme, height=58,
            stops=[(0.0, col), (1.0, mix(col, theme.colors["bg"], 0.75))],
            title="Ground Truth",
            subtitle="What the project data actually says — every number "
                     "computed locally, every insight traceable to a rule")
        self.header.pack(fill="x")

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        tiles = ttk.Frame(body)
        tiles.pack(fill="x")
        self.tile_defs = [
            ("rfis_open", "RFIs not verified", "#d64545"),
            ("tasks_open", "Open tasks", "#3f6fe0"),
            ("tasks_overdue", "Overdue tasks", "#d99c20"),
            ("punch_open", "Punch open", "#8b5cf6"),
            ("co_pending", "COs pending", "#12a5ba"),
        ]
        self.tiles = {}
        for i, (key, cap, color) in enumerate(self.tile_defs):
            t = KpiTile(tiles, theme, cap, color)
            t.grid(row=0, column=i, padx=(0, 12), sticky="nsew")
            tiles.columnconfigure(i, weight=1)
            self.tiles[key] = t

        # the Heartwood card row (the Corral's growth gauges): built here,
        # PACKED only when a store file already exists on disk — a fresh
        # install shows no card and raises nothing, the same silent-empty
        # rule the project tiles follow
        self.hw_path_provider = None       # app wires this to the Old Hand
        self.hw_frame = ttk.Frame(body)
        ttk.Label(self.hw_frame, text="▍Heartwood",
                  font=("Segoe UI", 13, "bold"), foreground=col
                  ).pack(anchor="w", pady=(0, 6))
        hw_tiles = ttk.Frame(self.hw_frame)
        hw_tiles.pack(fill="x")
        self.hw_tile_defs = [
            ("kb_mb", "KB size (MB)", "#12a5ba"),
            ("passages", "Passages", "#2f9e62"),
            ("queue", "Unverified queue", "#d99c20"),
            ("asks_7d", "Asks, 7 days", "#8b5cf6"),
        ]
        self.hw_tiles = {}
        for i, (key, cap, color) in enumerate(self.hw_tile_defs):
            t = KpiTile(hw_tiles, theme, cap, color)
            t.grid(row=0, column=i, padx=(0, 12), sticky="nsew")
            hw_tiles.columnconfigure(i, weight=1)
            self.hw_tiles[key] = t
        self.hw_tiles["kb_mb"].counter.fmt = "{:,.1f}"

        mid = ttk.Frame(body)
        mid.pack(fill="both", expand=True, pady=(14, 0))
        self._mid = mid

        gauges = ttk.Frame(mid)
        gauges.pack(side="left", anchor="n", padx=(0, 18))
        ttk.Label(gauges, text="▍Vitals", font=("Segoe UI", 13, "bold"),
                  foreground=col).pack(anchor="w", pady=(0, 6))
        self.g_answer = fx.Meter(gauges, theme, color="#3f6fe0",
                                 label="RFIs answered")
        self.g_answer.pack(pady=4)
        self.g_resolve = fx.Meter(gauges, theme, color="#2f9e62",
                                  label="RFIs verified")
        self.g_resolve.pack(pady=4)
        self.g_budget = fx.Meter(gauges, theme, color="#d99c20",
                                 label="budget spent")
        self.g_budget.pack(pady=4)

        feed_frame = ttk.Frame(mid)
        feed_frame.pack(side="left", fill="both", expand=True)
        ttk.Label(feed_frame, text="▍Insight feed",
                  font=("Segoe UI", 13, "bold"),
                  foreground=col).pack(anchor="w", pady=(0, 6))
        self.feed = tk.Text(feed_frame, height=14, wrap="word",
                            state="disabled", font=("Segoe UI", 10),
                            spacing3=7)
        self.feed.pack(fill="both", expand=True)
        for kind, color in INSIGHT_COLOR.items():
            self.feed.tag_configure(kind, foreground=color,
                                    font=("Segoe UI", 10, "bold"))

        def _style_feed(c):
            theme.style_text(self.feed)
            # the rule tag uses the theme's muted tone — re-apply on every
            # theme change or it goes unreadable after a light/dark toggle
            self.feed.tag_configure("rule", foreground=c["muted"],
                                    font=("Segoe UI", 8))
        theme.register(_style_feed)

        ttk.Button(body, text="⟳ Recompute", command=self.refresh).pack(
            anchor="e", pady=(8, 0))

    # ---------------------------------------------------------------- data
    def _resolution_counts(self):
        board = self.project_sec.board
        store = board._ensure_store()
        return store.counts() if store else {}

    def refresh(self):
        proj = self.get_project()
        summ = proj.summary() if proj else {}
        res = self._resolution_counts()
        total_rfis = sum(res.values())
        not_verified = total_rfis - res.get("verified", 0)
        answered_plus = total_rfis - res.get("open", 0)

        hist = self._histories(proj)
        self.tiles["rfis_open"].set(not_verified, hist.get("rfis"))
        self.tiles["tasks_open"].set(summ.get("tasks_open", 0),
                                     hist.get("tasks"))
        self.tiles["tasks_overdue"].set(summ.get("tasks_overdue", 0))
        self.tiles["punch_open"].set(summ.get("punch_open", 0),
                                     hist.get("punch"))
        self.tiles["co_pending"].set(summ.get("co_pending", 0))

        self.g_answer.set(answered_plus / total_rfis * 100 if total_rfis else 0)
        self.g_resolve.set(res.get("verified", 0) / total_rfis * 100
                           if total_rfis else 0)
        btot = summ.get("budget_total", 0)
        self.g_budget.set(summ.get("budget_spent", 0) / btot * 100
                          if btot else 0)
        self._insights(proj, summ, res)
        self._hw_refresh()

    # ------------------------------------------------------ heartwood card
    def _hw_path(self):
        try:
            if self.hw_path_provider is not None:
                return self.hw_path_provider()
            from .. import heartwood
            return heartwood.default_path()
        except Exception:
            return None

    def _hw_refresh(self):
        """Show the Heartwood card only when a store file already exists —
        opening a store CREATES one, so a missing file means hide, never
        touch.  Any read trouble hides the card too (no error boxes)."""
        path = self._hw_path()
        if not path or path == ":memory:" or not os.path.isfile(path):
            self.hw_frame.pack_forget()
            return
        try:
            from ..heartwood import corral
            from ..heartwood.store import HeartwoodStore
            st = HeartwoodStore(path)
            try:
                g = corral.gauges(st)
            finally:
                st.close()
        except Exception:
            self.hw_frame.pack_forget()
            return
        if not self.hw_frame.winfo_manager():
            self.hw_frame.pack(fill="x", pady=(14, 0), before=self._mid)
        notes = g.get("notes", {})
        queue = (int(notes.get("unverified", 0))
                 + int(g.get("proposals_pending", 0)))
        self.hw_tiles["kb_mb"].set(g.get("db_size_mb", 0.0))
        self.hw_tiles["passages"].set(g.get("chunks", 0),
                                      g.get("growth") or None)
        self.hw_tiles["queue"].set(queue)
        self.hw_tiles["asks_7d"].set(g.get("asks_7d", 0))

    def _histories(self, proj):
        """Simple created-date histograms so sparklines show real shape."""
        if not proj:
            return {}
        out = {}
        for key, kind, datefld in (("tasks", "tasks", "created"),
                                   ("punch", "punch", "created")):
            buckets = [0] * 8
            today = _dt.date.today()
            for it in proj.items(kind):
                try:
                    d = _dt.date.fromisoformat(
                        (getattr(it, datefld, "") or "")[:10])
                    weeks = min(7, max(0, (today - d).days // 7))
                    buckets[7 - weeks] += 1
                except ValueError:
                    continue
            if any(buckets):
                out[key] = buckets
        return out

    def _insights(self, proj, summ, res):
        items = []

        def add(kind, text, rule):
            items.append((kind, text, rule))

        stamp = self.project_sec.stamp
        if res:
            if res.get("open", 0):
                add("risk", f"{res['open']} RFI(s) still unanswered — the "
                            "crews may be building from stale details.",
                    "rule: resolution status == open")
            if res.get("fixed", 0):
                add("watch", f"{res['fixed']} fix(es) await field "
                             "verification — walk them and flip to VERIFIED.",
                    "rule: status == fixed")
            if res.get("verified", 0) == sum(res.values()) and res:
                add("good", "Every stamped RFI is verified in the field. "
                            "The set is clean.", "rule: all == verified")
        elif stamp.rows:
            add("watch", "RFIs scanned but the Resolution Board isn't "
                         "synced — statuses aren't tracking yet.",
                "rule: rows without resolution store")
        if summ:
            if summ.get("tasks_overdue", 0):
                add("risk", f"{summ['tasks_overdue']} task(s) past due.",
                    "rule: due < today and status != done")
            if summ.get("schedule_behind", 0):
                add("risk", f"{summ['schedule_behind']} schedule activit"
                            "y(ies) past their end date under 100%.",
                    "rule: end < today and pct < 100")
            if summ.get("inspections_failed", 0):
                add("watch", f"{summ['inspections_failed']} failed "
                             "inspection(s) on record.",
                    "rule: inspection status == failed")
            btot = summ.get("budget_total", 0)
            if btot and summ.get("budget_spent", 0) > 0.9 * btot:
                add("watch", "Budget is over 90% spent.",
                    "rule: spent > 0.9 × budget")
            if not items:
                add("good", "No risk rules firing — the project data looks "
                            "healthy.", "rule: none matched")
        if not items:
            add("watch", "No project data yet. Create a project on Home, "
                         "scan RFIs, and this feed lights up.",
                "rule: empty stores")

        self.feed.configure(state="normal")
        self.feed.delete("1.0", "end")
        for kind, text, rule in items:
            self.feed.insert("end", f"{INSIGHT_GLYPH[kind]} ", kind)
            self.feed.insert("end", text + "\n")
            self.feed.insert("end", f"    {rule}\n", "rule")
        self.feed.configure(state="disabled")

    def commands(self):
        return [("Recompute Ground Truth", "Ground Truth", self.refresh)]
