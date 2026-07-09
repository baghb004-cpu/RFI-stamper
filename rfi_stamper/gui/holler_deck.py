"""Holler: the hands-free voice companion — a floating, always-on-top deck
that types into whatever window has focus.

Say a measurement and the **Caller** types it formatted; say a tool word and
a **Trip** fires its shortcut; say a phrase and a **Placard** stamps the exact
text; say a macro name and a **Run** plays the keystroke sequence.  The
**Songbook** is the editable command dictionary; the **Ticker** tapes what it
heard, what it did, and the keystrokes it saved.

The ear is the Squawk Box recognizer (trained in your own voice, any
language); this window is the brain + hands.  Honest boundary: the keystroke
sender is a Windows OS call — on other platforms it runs in DRY mode
(recording the exact keystrokes it *would* send), and the UI says so.
Planloom's own process opens zero network sockets; opening a target is a local
OS hand-off (URL targets are opt-in per row and clearly flagged).
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import holler
from .theme import mix, section_color
from .widgets import make_tree, open_path, toast

KIND_LABEL = {"trip": "Trip · tool shortcut", "placard": "Placard · text",
              "fetch": "Fetch · open target", "run": "Run · macro"}


class HollerDeck:
    """A single floating companion window (the app keeps one instance)."""

    def __init__(self, root, theme, status=None):
        self.root, self.theme, self.status = root, theme, status
        self.deck_dir = os.path.join(os.path.expanduser("~"), ".planloom",
                                     "hollerdeck")
        self.songbook = holler.Songbook()
        sb_path = os.path.expanduser(holler.default_path())
        if os.path.exists(sb_path):
            try:
                self.songbook.load(sb_path)
            except Exception:   # noqa: BLE001 -- corrupt songbook -> seed
                self.songbook = holler.Songbook.seed()
        else:
            self.songbook = holler.Songbook.seed()
            self._save_songbook()
        self.ticker = holler.Ticker(holler.ticker_default_path())
        self.router = holler.Holler(songbook=self.songbook,
                                    ticker=self.ticker)

        w = tk.Toplevel(root)
        self.win = w
        w.title("Holler — hands-free control")
        w.geometry("560x620")
        try:
            w.attributes("-topmost", True)     # a companion over your CAD
        except tk.TclError:
            pass
        w.protocol("WM_DELETE_WINDOW", self.close)

        col = section_color("plans")
        head = tk.Frame(w)
        self._head = head
        head.pack(fill="x")
        self._title = tk.Label(head, text="⟟  Holler",
                               font=("Segoe UI", 15, "bold"))
        self._title.pack(side="left", padx=12, pady=8)
        self._send_lbl = tk.Label(
            head, font=("Segoe UI", 8, "bold"),
            text=("● types into any window" if holler.HAS_SEND else
                  "○ DRY — preview only (real keystrokes need Windows)"))
        self._send_lbl.pack(side="right", padx=12)

        body = ttk.Frame(w, padding=10)
        body.pack(fill="both", expand=True)

        # profile + voice
        top = ttk.Frame(body)
        top.pack(fill="x")
        ttk.Label(top, text="Dimension format", style="Muted.TLabel"
                  ).pack(side="left")
        self.profile = tk.StringVar(value="arch")
        cb = ttk.Combobox(top, width=14, state="readonly",
                          textvariable=self.profile,
                          values=list(holler.PROFILES))
        cb.pack(side="left", padx=(4, 0))
        cb.bind("<<ComboboxSelected>>",
                lambda _e: self.router.set_profile(self.profile.get()))
        ttk.Button(top, text="🎙 Voice…", command=self.open_voice
                   ).pack(side="right")

        # manual utterance box — always works, mic or not
        say = ttk.Frame(body)
        say.pack(fill="x", pady=(8, 4))
        ttk.Label(say, text="Say / type", style="Muted.TLabel"
                  ).pack(side="left")
        self.utter = tk.StringVar()
        e = ttk.Entry(say, textvariable=self.utter)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda _e: self.dispatch(self.utter.get()))
        ttk.Button(say, text="Holler it", style="Accent.TButton",
                   command=lambda: self.dispatch(self.utter.get())).pack(
            side="left")

        # the Ticker
        tick = ttk.Frame(body)
        tick.pack(fill="both", expand=True, pady=(6, 4))
        trow = ttk.Frame(tick)
        trow.pack(fill="x")
        ttk.Label(trow, text="The Ticker", font=("Segoe UI", 10, "bold")
                  ).pack(side="left")
        self.counter = ttk.Label(trow, style="Muted.TLabel", text="")
        self.counter.pack(side="left", padx=8)
        ttk.Button(trow, text="Reset", style="Tool.TButton",
                   command=self.reset_ticker).pack(side="right")
        self.tape = tk.Text(tick, height=8, wrap="word", state="disabled",
                            relief="flat", font=("Consolas", 9))
        self.tape.pack(fill="both", expand=True, pady=(4, 0))

        # the Songbook editor
        sb = ttk.Frame(body)
        sb.pack(fill="both", expand=True)
        srow = ttk.Frame(sb)
        srow.pack(fill="x")
        ttk.Label(srow, text="The Songbook", font=("Segoe UI", 10, "bold")
                  ).pack(side="left")
        for label, cmd in (("Open as spreadsheet", self.open_csv),
                           ("Import CSV…", self.import_csv),
                           ("Delete", self.del_entry),
                           ("Edit…", self.edit_entry),
                           ("Add…", self.add_entry)):
            ttk.Button(srow, text=label, style="Tool.TButton",
                       command=cmd).pack(side="right", padx=1)
        frame, self.tree = make_tree(
            sb, theme, [("trigger", "SAY"), ("kind", "DOES"),
                        ("payload", "DETAIL")], (140, 130, 200), height=8)
        frame.pack(fill="both", expand=True, pady=(4, 0))
        self.tree.bind("<Double-Button-1>", lambda _e: self.edit_entry())

        theme.register(self._on_theme)
        self._fill_songbook()
        self._update_counter()

    # -------------------------------------------------------------- theme
    def _on_theme(self, c):
        if not self.win.winfo_exists():
            return
        self.win.configure(bg=c["bg"])
        self._head.configure(bg=c["panel"])
        self._title.configure(bg=c["panel"], fg=c["fg"])
        self._send_lbl.configure(
            bg=c["panel"], fg=c["ok"] if holler.HAS_SEND else c["muted"])
        self.tape.configure(bg=c["log_bg"], fg=c["fg"],
                            insertbackground=c["fg"])
        self.tape.tag_configure("hit", foreground=c["ok"])
        self.tape.tag_configure("miss", foreground=c["muted"])
        self.tape.tag_configure("saved", foreground=c["accent"])

    # ----------------------------------------------------------- dispatch
    def open_voice(self):
        """The ear: the Squawk Box recognizer, trained on the Holler deck."""
        try:
            from .squawk_deck import SquawkDialog
            SquawkDialog(self.win, self.theme, self.dispatch,
                         deck_dir=self.deck_dir)
        except Exception as e:      # noqa: BLE001 -- audio stack optional
            messagebox.showinfo("Holler — voice",
                                f"Voice input is unavailable here:\n{e}\n\n"
                                "The type box works without a microphone.")

    def dispatch(self, utterance):
        u = (utterance or "").strip()
        if not u:
            return
        self.utter.set("")
        try:
            res = self.router.dispatch(u)
        except Exception as e:      # noqa: BLE001 -- never crash the deck
            self._tape(f"! {u} — {e}\n", "miss")
            return
        matched = res.get("matched", "miss")
        detail = res.get("detail", "")
        saved = res.get("keystrokes_saved", 0)
        if matched == "miss":
            self._tape(f"? {u}  — not in the Songbook, not a measurement\n",
                       "miss")
        else:
            verb = {"trip": "fired", "placard": "typed", "fetch": "opened",
                    "run": "ran", "dimension": "typed", "shape": "typed"
                    }.get(matched, "did")
            dry = "" if holler.HAS_SEND else "  [preview]"
            self._tape(f"“{u}” → {verb}: {detail}{dry}", "hit")
            if saved:
                self._tape(f"    (+{saved} keystrokes)\n", "saved")
            else:
                self._tape("\n")
            if res.get("note"):
                self._tape(f"    {res['note']}\n", "miss")
        self._update_counter()

    def _tape(self, text, tag=None):
        self.tape.configure(state="normal")
        self.tape.insert("end", text, (tag,) if tag else ())
        self.tape.configure(state="disabled")
        self.tape.see("end")

    def _update_counter(self):
        s = self.ticker.summary()
        self.counter.configure(
            text=f"{s['commands']} command(s) · {s['keystrokes_saved']} "
                 f"keystrokes saved")

    def reset_ticker(self):
        self.ticker.reset()
        self.tape.configure(state="normal")
        self.tape.delete("1.0", "end")
        self.tape.configure(state="disabled")
        self._update_counter()

    # ----------------------------------------------------------- songbook
    def _fill_songbook(self):
        self.tree.delete(*self.tree.get_children())
        for i, en in enumerate(self.songbook.entries):
            detail = en.payload
            if en.kind == "run":
                detail = " · ".join(f"{s[0]}:{s[1]}" for s in en.steps
                                    ) if en.steps else "(macro)"
            if en.is_url:
                detail += "  [URL]"
            self.tree.insert("", "end", iid=str(i),
                             values=(en.trigger,
                                     KIND_LABEL.get(en.kind, en.kind),
                                     detail))

    def _save_songbook(self):
        try:
            self.songbook.save(holler.default_path())
        except Exception as e:      # noqa: BLE001
            if self.status:
                self.status.set(f"Songbook save failed: {e}", "err")

    def _sel_entry(self):
        sel = self.tree.selection()
        if not sel:
            return None
        i = int(sel[0])
        return self.songbook.entries[i] if 0 <= i < len(
            self.songbook.entries) else None

    def add_entry(self):
        self._entry_dialog(None)

    def edit_entry(self):
        en = self._sel_entry()
        if en is not None:
            self._entry_dialog(en)

    def del_entry(self):
        en = self._sel_entry()
        if en is None:
            return
        self.songbook.remove(en.trigger)
        self._save_songbook()
        self.router.reload_songbook()
        self._fill_songbook()

    def _entry_dialog(self, en):
        dlg = tk.Toplevel(self.win)
        dlg.title("Songbook entry")
        dlg.transient(self.win)
        try:
            dlg.attributes("-topmost", True)
        except tk.TclError:
            pass
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        trig = tk.StringVar(value=en.trigger if en else "")
        kind = tk.StringVar(value=en.kind if en else "trip")
        pay = tk.StringVar(value=en.payload if en else "")
        steps = tk.StringVar(value=(" | ".join(f"{s[0]}:{s[1]}"
                                    for s in en.steps) if en and en.steps
                                    else ""))
        isurl = tk.BooleanVar(value=bool(en.is_url) if en else False)

        def rowf(label, var, width=34):
            r = ttk.Frame(frm)
            r.pack(fill="x", pady=2)
            ttk.Label(r, text=label, width=10, style="Muted.TLabel"
                      ).pack(side="left")
            ttk.Entry(r, textvariable=var, width=width).pack(
                side="left", fill="x", expand=True)

        rowf("Say", trig)
        r = ttk.Frame(frm)
        r.pack(fill="x", pady=2)
        ttk.Label(r, text="Does", width=10, style="Muted.TLabel"
                  ).pack(side="left")
        ttk.Combobox(r, textvariable=kind, state="readonly", width=10,
                     values=["trip", "placard", "fetch", "run"]).pack(
            side="left")
        rowf("Detail", pay)
        rowf("Run steps", steps)
        ttk.Label(frm, style="Muted.TLabel", wraplength=360,
                  text="Trip: a shortcut like  ctrl+c  or  l+Enter · "
                       "Placard: the exact text · Fetch: a file/folder path · "
                       "Run steps:  type:e | wait:1.0 | key:Tab | type:90 | "
                       "key:Enter").pack(anchor="w", pady=(4, 6))
        ttk.Checkbutton(frm, text="this Fetch opens a URL (launches your "
                                  "browser — a separate program)",
                        variable=isurl).pack(anchor="w")

        def save():
            t = trig.get().strip()
            if not t:
                return
            step_list = []
            for chunk in steps.get().split("|"):
                chunk = chunk.strip()
                if ":" in chunk:
                    verb, _, val = chunk.partition(":")
                    step_list.append([verb.strip(), val.strip()])
            if en is not None:
                self.songbook.remove(en.trigger)
            self.songbook.add(holler.Entry(
                trigger=t, kind=kind.get(), payload=pay.get().strip(),
                steps=step_list, is_url=isurl.get()))
            self._save_songbook()
            self.router.reload_songbook()
            self._fill_songbook()
            dlg.destroy()

        btn = ttk.Frame(frm)
        btn.pack(fill="x", pady=(8, 0))
        ttk.Button(btn, text="Save", style="Accent.TButton",
                   command=save).pack(side="right")
        ttk.Button(btn, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=4)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def open_csv(self):
        """Tune the Songbook in any spreadsheet — export, edit, re-import."""
        path = os.path.join(self.deck_dir, "songbook.csv")
        os.makedirs(self.deck_dir, exist_ok=True)
        try:
            self.songbook.to_csv(path)
        except Exception as e:      # noqa: BLE001
            messagebox.showinfo("Holler", f"Could not write CSV:\n{e}")
            return
        open_path(path)
        toast(self.win, self.theme,
              "Songbook CSV written — edit it, then Import CSV to load")

    def import_csv(self):
        p = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if not p:
            return
        try:
            self.songbook.from_csv(p)
        except Exception as e:      # noqa: BLE001
            messagebox.showinfo("Holler", f"Import failed:\n{e}")
            return
        self._save_songbook()
        self.router.reload_songbook()
        self._fill_songbook()
        toast(self.win, self.theme, "Songbook imported")

    # -------------------------------------------------------------- window
    def show(self):
        if self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()

    def close(self):
        try:
            self.ticker.save()
        except Exception:   # noqa: BLE001 -- ticker persistence is optional
            pass
        if self.win.winfo_exists():
            self.win.destroy()
