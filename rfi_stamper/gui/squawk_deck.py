"""The Squawk Box dialog: Planloom's mic/headset voice-command deck.

Meeting-app manners, job-site honesty: an input-device picker with a live
level meter, push-to-talk (hold the big button or F9), and a training pane
where YOU record 2–3 takes per phrase — those recordings ARE the model
(:mod:`rfi_stamper.squawk`, MFCC + DTW, fully offline).  A confident match
fires ``on_command(text)`` — the Loft feeds the phrase straight into the
Weave bar; an unconfident one shows "did you mean…" candidates and asks.
It never guesses a drawing command.

Capture is Windows-only (wave-in via ctypes); everywhere else the dialog
says so plainly and keeps the deck browsable/trainable from stored takes.
The level meter polls ONLY while recording — no free-running loops.
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk

import numpy as np

from .widgets import make_tree

DEFAULT_DECK_DIR = os.path.join("~", ".planloom", "squawkdeck")
MIN_UTTER_S = 0.15         # anything shorter is a fumbled press, not a phrase


class SquawkDialog:
    """Toplevel voice-command deck.  ``on_command(text)`` receives every
    confident (or clicked) phrase; a broken audio stack only ever degrades
    to honest status text."""

    def __init__(self, root, theme, on_command, deck_dir: str = None):
        from .. import squawk
        self.squawk = squawk
        self.root, self.theme = root, theme
        self.on_command = on_command
        self.rate = 16000
        self._rec = None
        self._rec_mode = None            # ("command", None) | ("train", text)
        self._poll_id = None

        path = os.path.expanduser(deck_dir or DEFAULT_DECK_DIR)
        self.deck = squawk.Deck(path)
        try:
            for text in squawk.SUGGESTED_PHRASES:    # seed the day-one deck
                self.deck.add_phrase(text)
        except Exception:   # noqa: BLE001 -- read-only disk: still usable
            pass
        try:
            self._devices = squawk.list_devices()
        except Exception:   # noqa: BLE001
            self._devices = []

        c = theme.colors
        dlg = tk.Toplevel(root)
        self.dlg = dlg
        dlg.title("Squawk Box — speaker-trained voice commands")
        dlg.transient(root)
        dlg.geometry("680x640")
        dlg.configure(bg=c["bg"])
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="🎙 Squawk Box", style="Title.TLabel"
                  ).pack(anchor="w")
        ttk.Label(frm, style="Muted.TLabel", wraplength=640, justify="left",
                  text="Recognizes the phrases YOU trained — push-to-talk, "
                       "speaker-dependent, fully offline.  Not open "
                       "dictation: anything unsure shows the closest "
                       "matches and asks, never guesses a command."
                  ).pack(anchor="w", pady=(0, 8))

        # ---- input device + level meter --------------------------------
        dev = ttk.Frame(frm)
        dev.pack(fill="x")
        ttk.Label(dev, text="Input:", style="Muted.TLabel").pack(side="left")
        self.dev_var = tk.StringVar()
        self.dev_box = ttk.Combobox(dev, textvariable=self.dev_var,
                                    state="readonly", width=36)
        self.dev_box.pack(side="left", padx=6)
        if self.squawk.HAS_CAPTURE:
            names = (["System default"]
                     + [d["name"] for d in self._devices])
            self.dev_box.configure(values=names)
            self.dev_box.current(0)
        else:
            self.dev_box.configure(values=["(no capture on this platform)"],
                                   state="disabled")
            self.dev_box.set("(no capture on this platform)")
        self.level = tk.Canvas(dev, height=14, highlightthickness=1,
                               highlightbackground=c["border"],
                               bg=c["panel"])
        self.level.pack(side="left", fill="x", expand=True, padx=(8, 0))
        if not self.squawk.HAS_CAPTURE:
            ttk.Label(frm, style="Muted.TLabel", wraplength=640,
                      justify="left",
                      text="Audio capture runs on the Windows wave-in "
                           "interface and is unavailable here — the deck "
                           "below stays browsable, and stored takes still "
                           "match.").pack(anchor="w", pady=(4, 0))

        # ---- push-to-talk ------------------------------------------------
        self.talk = tk.Label(frm, text="●  Hold to talk   (or hold F9)",
                             font=("Segoe UI", 14, "bold"),
                             bg=c["accent"], fg=c["accent_fg"],
                             pady=14, cursor="hand2")
        self.talk.pack(fill="x", pady=(10, 4))
        self.talk.bind("<ButtonPress-1>", lambda e: self._press("command"))
        self.talk.bind("<ButtonRelease-1>", lambda e: self._release())
        dlg.bind("<KeyPress-F9>", lambda e: self._press("command"))
        dlg.bind("<KeyRelease-F9>", lambda e: self._release())

        self.say = ttk.Label(frm, style="Muted.TLabel", wraplength=640,
                             justify="left",
                             text="hold the button, speak one trained "
                                  "phrase, release")
        self.say.pack(anchor="w", pady=(2, 2))
        self.cands = ttk.Frame(frm)          # "did you mean…" buttons
        self.cands.pack(fill="x")

        # ---- training pane -------------------------------------------------
        box = ttk.Labelframe(frm, text="Train the deck — the takes ARE the "
                                       "model (2–3 per phrase)")
        box.pack(fill="both", expand=True, pady=(10, 0))
        inner = ttk.Frame(box, padding=8)
        inner.pack(fill="both", expand=True)
        tf, self.tree = make_tree(inner, theme,
                                  [("phrase", "PHRASE"),
                                   ("takes", "TAKES")],
                                  (420, 60), height=8)
        tf.pack(fill="both", expand=True)

        addrow = ttk.Frame(inner)
        addrow.pack(fill="x", pady=(6, 0))
        self.new_var = tk.StringVar()
        ent = ttk.Entry(addrow, textvariable=self.new_var)
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self._add_phrase())
        ttk.Button(addrow, text="Add phrase", style="Tool.TButton",
                   command=self._add_phrase).pack(side="left", padx=(4, 0))

        btns = ttk.Frame(inner)
        btns.pack(fill="x", pady=(6, 0))
        self.rec_btn = tk.Label(btns, text="●  Record take (hold)",
                                font=("Segoe UI", 10, "bold"),
                                bg=c["panel"], fg=c["err"],
                                padx=12, pady=5, cursor="hand2")
        self.rec_btn.pack(side="left")
        self.rec_btn.bind("<ButtonPress-1>", lambda e: self._press("train"))
        self.rec_btn.bind("<ButtonRelease-1>", lambda e: self._release())
        ttk.Button(btns, text="▶ Play", style="Tool.TButton",
                   command=self._play).pack(side="left", padx=4)
        ttk.Button(btns, text="Delete take", style="Tool.TButton",
                   command=self._delete_take).pack(side="left")
        ttk.Label(btns, style="Muted.TLabel",
                  text="  stored as plain WAVs in " + path).pack(side="left")

        dlg.protocol("WM_DELETE_WINDOW", self._close)
        dlg.bind("<Escape>", lambda e: self._close())
        self._refresh_tree()
        self._draw_level(0.0)

    # ------------------------------------------------------------- helpers --
    def _status(self, text: str) -> None:
        if self.say.winfo_exists():
            self.say.configure(text=text)

    def _selected_phrase(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.item(sel[0], "values")[0]

    def _refresh_tree(self) -> None:
        try:
            phrases = self.deck.phrases()
        except Exception as e:   # noqa: BLE001
            self._status(f"the deck is unreadable: {e}")
            return
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for p in phrases:
            self.tree.insert("", "end", iid=p["slug"],
                             values=(p["text"], p["takes"]))
        for iid in keep:
            if self.tree.exists(iid):
                self.tree.selection_set(iid)

    def _device_id(self):
        if not self.squawk.HAS_CAPTURE:
            return None
        i = self.dev_box.current()
        if i <= 0 or i - 1 >= len(self._devices):
            return None                     # "System default"
        return self._devices[i - 1]["id"]

    # ------------------------------------------------------- push-to-talk --
    def _press(self, mode: str) -> None:
        if self._rec is not None:           # key auto-repeat / double press
            return
        if not self.squawk.HAS_CAPTURE:
            self._status("no microphone path on this platform — capture "
                         "needs the Windows wave-in interface")
            return
        phrase = None
        if mode == "train":
            phrase = self._selected_phrase()
            if not phrase:
                self._status("pick a phrase in the list first, then hold "
                             "Record take")
                return
        try:
            rec = self.squawk.Recorder(device_id=self._device_id(),
                                       rate=self.rate)
            rec.start()
        except Exception as e:   # noqa: BLE001 -- honest, never fatal
            self._status(f"could not open the microphone: {e}")
            return
        self._rec = rec
        self._rec_mode = (mode, phrase)
        self._status("● listening — release to "
                     + ("match" if mode == "command"
                        else f"save a take of “{phrase}”"))
        self._poll_level()                  # poll ONLY while recording

    def _release(self) -> None:
        rec, self._rec = self._rec, None
        self._stop_poll()
        self._draw_level(0.0)
        if rec is None:
            return
        mode, phrase = self._rec_mode or ("command", None)
        self._rec_mode = None
        try:
            data = rec.stop()
        except Exception as e:   # noqa: BLE001
            self._status(f"recording failed: {e}")
            return
        samples = np.frombuffer(data[:len(data) - (len(data) % 2)],
                                dtype="<i2")
        if len(samples) < int(self.rate * MIN_UTTER_S):
            self._status("too short — keep holding while you speak")
            return
        if mode == "train":
            try:
                self.deck.add_take(phrase, self.rate, samples)
            except Exception as e:   # noqa: BLE001
                self._status(f"could not save the take: {e}")
                return
            self._refresh_tree()
            n = len(self.deck.take_paths(phrase))
            self._status(f"take saved — {n} take(s) of “{phrase}”"
                         + ("  (one more take makes it solid)"
                            if n < 2 else ""))
            return
        try:
            matches = self.deck.match(self.rate, samples)
        except Exception as e:   # noqa: BLE001
            self._status(f"matching failed: {e}")
            return
        self._show_matches(matches)

    # ---------------------------------------------------------- matching --
    def _show_matches(self, matches) -> None:
        for w in self.cands.winfo_children():
            w.destroy()
        if not matches:
            self._status("nothing trained yet — record 2–3 takes per "
                         "phrase below, then talk")
            return
        if self.squawk.confident(matches):
            self._fire(matches[0]["text"])
            return
        self._status("not sure — did you mean:")
        for m in matches:
            ttk.Button(self.cands,
                       text=f"{m['text']}   ({m['score']:.2f})",
                       style="Tool.TButton",
                       command=lambda t=m["text"]: self._fire(t)
                       ).pack(side="left", padx=2, pady=2)

    def _fire(self, text: str) -> None:
        for w in self.cands.winfo_children():
            w.destroy()
        try:
            self.on_command(text)
        except Exception as e:   # noqa: BLE001 -- the deck outlives the verb
            self._status(f"“{text}” didn't run: {e}")
            return
        self._status(f"✓ heard “{text}” — sent to the board")

    # ------------------------------------------------------------ training --
    def _add_phrase(self) -> None:
        text = self.new_var.get().strip()
        if not text:
            return
        try:
            p = self.deck.add_phrase(text)
        except Exception as e:   # noqa: BLE001
            self._status(f"could not add the phrase: {e}")
            return
        self.new_var.set("")
        self._refresh_tree()
        if self.tree.exists(p["slug"]):
            self.tree.selection_set(p["slug"])
        self._status(f"added “{p['text']}” — now hold Record take "
                     "and say it")

    def _play(self) -> None:
        phrase = self._selected_phrase()
        if not phrase:
            self._status("pick a phrase to play back")
            return
        try:
            paths = self.deck.take_paths(phrase)
        except Exception as e:   # noqa: BLE001
            self._status(f"deck error: {e}")
            return
        if not paths:
            self._status(f"no takes of “{phrase}” yet")
            return
        if sys.platform == "win32":
            try:
                import winsound
                winsound.PlaySound(paths[-1], winsound.SND_FILENAME
                                   | winsound.SND_ASYNC)
                self._status(f"playing the last take of “{phrase}”")
            except Exception as e:   # noqa: BLE001
                self._status(f"playback failed: {e}")
        else:
            self._status("playback is wired to the Windows sound API — "
                         "silent on this platform (take is at "
                         + paths[-1] + ")")

    def _delete_take(self) -> None:
        phrase = self._selected_phrase()
        if not phrase:
            self._status("pick a phrase first")
            return
        try:
            ok = self.deck.remove_take(phrase)
        except Exception as e:   # noqa: BLE001
            self._status(f"could not delete the take: {e}")
            return
        self._refresh_tree()
        self._status(f"deleted the last take of “{phrase}”" if ok
                     else f"no takes of “{phrase}” to delete")

    # --------------------------------------------------------- level meter --
    def _poll_level(self) -> None:
        self._poll_id = None
        if self._rec is None or not self.dlg.winfo_exists():
            return                          # stopped: the loop dies here
        try:
            lv = self._rec.level()
        except Exception:   # noqa: BLE001
            lv = 0.0
        self._draw_level(lv)
        self._poll_id = self.dlg.after(66, self._poll_level)

    def _stop_poll(self) -> None:
        if self._poll_id is not None:
            try:
                self.dlg.after_cancel(self._poll_id)
            except Exception:   # noqa: BLE001
                pass
            self._poll_id = None

    def _draw_level(self, lv: float) -> None:
        if not self.level.winfo_exists():
            return
        c = self.theme.colors
        cv = self.level
        cv.delete("all")
        w = max(cv.winfo_width(), 40)
        frac = min(1.0, max(0.0, lv)) ** 0.5     # perceptual-ish ramp
        if frac > 0.0:
            cv.create_rectangle(0, 0, int(w * frac), 20, width=0,
                                fill=c["ok"] if frac < 0.85 else c["warn"])

    # ---------------------------------------------------------------- close --
    def _close(self) -> None:
        rec, self._rec = self._rec, None
        self._stop_poll()
        if rec is not None:
            try:
                rec.stop()
            except Exception:   # noqa: BLE001
                pass
        if self.dlg.winfo_exists():
            self.dlg.destroy()
