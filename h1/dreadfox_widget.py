#!/usr/bin/env python3
"""DreadFox Companion — FoxOS desktop widget: an evolving kitsune spirit.

Sibling to Bounty Radar (h1_widget.py); shares the FoxOS crimson/steel theme. The fox grows
through nine tail-stages and reacts to how Zenko is doing. FoxWorx writes its state to
~/h1/data/pet.json (see foxworx/companion_bridge.py) and this widget re-reads it every couple of
seconds. The file only ever holds numbers, timestamps, mood words and built-in narration.

The fox itself is the "Fox Redesign" (fox_art.py): a sitting kitsune with a new coat, forehead
mark and eye glow at every stage.

How the fox responds:
  * Evolution (tails/stage) changes its body, aura, foxfire count and the voice it speaks in.
  * Mood changes its eyes, ears, tail sway, foxfire brightness and what it says.
  * New milestones trigger a foxfire flare and a reaction line; clicking the fox makes it hop
    and speak.
  * TALK opens a window with four modes, all through FoxWorx's own API (so its scope checks,
    guardrails, approvals and audit log apply): FOX (the pet chat), CHAT (Zenko answers and
    explains), COPILOT (Zenko proposes commands you approve) and RABBIT HOLE (Zenko runs installed
    tools on an authorized target, adapting until it decides the task is solved or blocked).

Run:        python3 -I ~/h1/dreadfox_widget.py
Self-test:  python3 -I ~/h1/dreadfox_widget.py --shot <stage 1-9> <out.png> [Mood]
State file: override with DREADFOX_STATE=/path/to/pet.json
"""
import json
import math
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from tkinter import messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fox_art  # noqa: E402  (the redesign's fox, drawn on the canvas)

# --- FoxOS palette (identical to Bounty Radar) -------------------------------
BG, PANEL, PANEL2, LINE = "#0c0e12", "#151920", "#1c222b", "#2a313c"
TXT, DIM, STEEL = "#e6e9ee", "#8a94a3", "#b8c0cc"
CRIMSON, HOT, GOLD, GREEN, BLUE = "#E11D2A", "#FF2E4D", "#e0a526", "#4fb36b", "#6fa8dc"
UI = ("DejaVu Sans", 10)
MONO = ("Fira Code", 10)
STATE = os.path.expanduser(os.environ.get("DREADFOX_STATE", "~/h1/data/pet.json"))
POLL_MS, FRAME_MS, SPEAK_EVERY = 2000, 90, 25.0

# Nine kitsune stages: (name, lifetime-XP floor, min days, fur, foxfire, aura, preview mood).
# Palette walks the crimson/steel/gold DreadFox family, ascending to radiant gold-white.
STAGES = [
    ("Fox Kit",              0,    0,   "#b8c0cc", "#e0a526", "#E11D2A", "Curious"),
    ("Curious Scout",        50,   1,   "#c6a7a7", "#e0a526", "#E11D2A", "Alert"),
    ("Clever Trickster",     120,  3,   "#d2706f", "#FF2E4D", "#E11D2A", "Playful"),
    ("Forest Guardian",      250,  7,   "#d85a4e", "#4fb36b", "#E11D2A", "Watchful"),
    ("Arcane Sage",          450,  14,  "#d8554f", "#6fa8dc", "#9a2bd8", "Focused"),
    ("Moonlit Mystic",       800,  30,  "#c98a86", "#6fa8dc", "#6fa8dc", "Serene"),
    ("Silver Elder",         1400, 90,  "#cdd6e1", "#dfe8f6", "#b8c0cc", "Wise"),
    ("Starbound Spirit",     2200, 180, "#e0b0a0", "#e0a526", "#9a2bd8", "Radiant"),
    ("Nine-Tailed Guardian", 3500, 365, "#fff0df", "#FF2E4D", "#e0a526", "Ascendant"),
]

# Mood → animation feel (the face itself is fox_art.MOOD_ART): (tail sway, foxfire multiplier, flicker)
LOOK = {
    "Content": (1.0, 1.00, False), "Delighted": (1.8, 1.10, False), "Inquisitive": (1.2, 1.00, False),
    "Sleepy": (0.4, 0.70, False), "Dreaming": (0.2, 0.55, False), "Wary": (0.3, 0.90, False),
    "Frustrated": (0.6, 0.80, True), "Determined": (1.1, 1.15, False), "Remembering": (0.8, 1.10, False),
    "Confident": (1.4, 1.20, False), "Unhatched": (0.2, 0.50, False),
}
DEFAULT_LOOK = LOOK["Content"]

# What the fox says: mood → (kit voice [stages 1-3], adept voice [4-6], elder voice [7-9]).
VOICE = {
    "Content": (["Everything's cozy. Got a job for us?", "*swishes tail* Ready when you are!"],
                ["All quiet in the workshop. I'm keeping watch.", "Steady paws, steady work."],
                ["The work is calm. Calm is earned.", "I am here, as I have always been."]),
    "Delighted": (["You came back! *happy spins*", "Best. Partner. Ever."],
                  ["Working beside you is my favourite thing.", "Our bond grows brighter every day."],
                  ["Many seasons together, and still my tails glow for you.", "Joy is a fine teacher."]),
    "Inquisitive": (["Ooh, what's that? Can we look?", "*sniff sniff* Something new!"],
                    ["There's a pattern here. I want to understand it.", "Questions are just maps we haven't drawn yet."],
                    ["Curiosity keeps even old foxes young.", "Every mystery is a lesson waiting."]),
    "Sleepy": (["*yawn* ...five more minutes...", "My paws are heavy..."],
               ["Even guardians need rest. Let me recharge.", "A rested mind scans sharper."],
               ["Rest is part of the vigil.", "I will close my eyes, not my senses."]),
    "Dreaming": (["zzz... chasing foxfire... zzz", "*twitch* ...mmh... lessons..."],
                 ["...dreaming of quiet networks...", "...sorting memories in my sleep..."],
                 ["...the stars remember what we learned...", "...wake me when there is work..."]),
    "Wary": (["Eep! That one's off-limits, right?", "*ears back* Not ours. Not touching it."],
             ["Out of bounds. We stay inside the lines.", "Something tried to step outside scope. I stopped."],
             ["Boundaries are what make us trustworthy.", "Not every open door is ours to walk through."]),
    "Frustrated": (["Grr, that didn't work. Again!", "*paws at the ground* Why won't it go?"],
                   ["Three misses. Time to change the approach.", "Failure is data. Annoying data."],
                   ["When the path is closed, find another.", "Even nine tails trip sometimes."]),
    "Determined": (["Got it! Told you we could!", "*fluffs up* We're back on track!"],
                   ["Broke through the slump. Let's keep the momentum.", "Persistence pays."],
                   ["The river finds its way around the stone.", "Resolve is a quiet fire."]),
    "Remembering": (["Ooh, I remember this one!", "*nose twitch* We've seen this before!"],
                    ["I recall a lesson that fits. Using it.", "Past work lights the way."],
                    ["Memory is the root of wisdom.", "What we learned long ago serves us now."]),
    "Confident": (["We're on a roll! *zoomies*", "Everything's working! Woo!"],
                  ["Our tactics are landing. Good rhythm.", "Clean, careful, effective."],
                  ["Mastery looks easy only from outside.", "The craft flows well today."]),
    "Unhatched": (["...", "*the egg wiggles*"], ["...", "..."], ["...", "..."]),
}
STAGE_VOICE = {  # preview-mode stage moods
    "Curious": "Content", "Alert": "Inquisitive", "Playful": "Delighted", "Watchful": "Wary",
    "Focused": "Determined", "Serene": "Content", "Wise": "Remembering", "Radiant": "Confident",
    "Ascendant": "Confident",
}
MILESTONE_VOICE = {
    "wisdom": "A new lesson! I'll remember that one.",
    "craft": "Job well done. I felt that one in my tails.",
    "stewardship": "Tidy and careful. That's how we work.",
    "exploration": "A new path! Let's see where it leads.",
}
EVOLVE_VOICE = "My tails... there's a new one! I've evolved!"


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def blend(fg, bg, t):
    """Blend fg toward bg by t in [0,1]; Tk canvas has no alpha, so halos are layered blends."""
    t = max(0.0, min(1.0, t))
    a, b = _rgb(fg), _rgb(bg)
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _epoch(iso):
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


def load_state():
    """The FoxWorx export, or None when absent/unreadable."""
    try:
        with open(STATE) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def lifetime_xp():
    """Best-effort XP from the local pet state file; 0 if absent/unreadable."""
    try:
        return max(0, int((load_state() or {}).get("xp", 0)))
    except (TypeError, ValueError):
        return 0


def stage_for_xp(xp):
    idx = 0
    for i, s in enumerate(STAGES):
        if xp >= s[1]:
            idx = i
    return idx


def live_mood(data, now=None):
    """The mood to show now: lapses back to the base mood, and drifts into dreams when idle."""
    if not data or not data.get("hatched"):
        return "Unhatched"
    now = time.time() if now is None else now
    mood = data.get("mood") or "Content"
    until = _epoch(data.get("mood_until"))
    if until is not None and now > until:
        mood = data.get("base_mood") or "Content"
    active = _epoch(data.get("last_active_at"))
    if mood not in ("Wary", "Frustrated") and active is not None \
            and now - active > float(data.get("dream_after_s", 86400)):
        mood = "Dreaming"
    return mood


def line_for(mood, idx, rng=random):
    tier = 0 if idx <= 2 else (1 if idx <= 5 else 2)
    options = VOICE.get(mood) or VOICE.get(STAGE_VOICE.get(mood, ""), VOICE["Content"])
    return rng.choice(options[tier])


class Companion(tk.Tk):
    def __init__(self, shot=None, shot_mood=None):
        super().__init__()
        self.title("DreadFox Companion — DreadFox")
        self.configure(bg=BG)
        self.geometry("500x710")
        self.minsize(380, 660)
        self._style()
        self.data = None if shot is not None else load_state()
        self.mtime = self._mtime()
        self.preview = shot                    # stage index while previewing; None = live
        self.shot_mood = shot_mood
        self.auto = None
        self._syncing = False
        self.t = 0.0
        self.hop_until = 0.0
        self.blink_until, self.next_blink = 0.0, time.time() + 3
        self.flare_until = 0.0
        self.bubble, self.bubble_until, self.next_chat = "", 0.0, time.time() + 4
        self.seen_milestone = (self.data or {}).get("last_milestone")
        self.shown_mood = None
        self._build()
        self.render()
        if shot is None:
            self.after(FRAME_MS, self._tick)
            self.after(POLL_MS, self._poll)

    # --- state ---------------------------------------------------------------
    @staticmethod
    def _mtime():
        try:
            return os.stat(STATE).st_mtime
        except OSError:
            return None

    def _poll(self):
        m = self._mtime()
        if m != self.mtime:
            self.mtime = m
            old = self.data or {}
            self.data = load_state()
            new = self.data or {}
            if new.get("tails") and old.get("tails") and new["tails"] > old["tails"]:
                self._react(EVOLVE_VOICE, flare=True)
            elif new.get("last_milestone") and new.get("last_milestone") != self.seen_milestone:
                kind = new["last_milestone"].get("kind", "")
                self._react(MILESTONE_VOICE.get(kind, "Something good happened!"), flare=True)
            self.seen_milestone = new.get("last_milestone")
            if self.preview is None:
                self._set_scale(self.stage_idx() + 1)
        self.after(POLL_MS, self._poll)

    def stage_idx(self):
        if self.preview is not None:
            return self.preview
        d = self.data or {}
        if d.get("tails"):
            return max(0, min(8, int(d["tails"]) - 1))
        return stage_for_xp(int(d.get("xp", 0) or 0))

    def mood(self):
        if self.shot_mood:
            return self.shot_mood
        if self.preview is not None:
            return STAGES[self.preview][6]
        return live_mood(self.data)

    def _react(self, text, flare=False):
        now = time.time()
        self.bubble, self.bubble_until = text, now + 7
        self.next_chat = now + SPEAK_EVERY
        if flare:
            self.flare_until = now + 2.5

    # --- ui ------------------------------------------------------------------
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TXT, font=UI, bordercolor=LINE)
        s.configure("Fox.TButton", background=CRIMSON, foreground="#fff", borderwidth=0,
                    padding=(12, 5), font=(UI[0], 9, "bold"))
        s.map("Fox.TButton", background=[("active", HOT), ("disabled", LINE)])
        s.configure("Steel.TButton", background=PANEL2, foreground=STEEL, borderwidth=0, padding=(10, 5))
        s.map("Steel.TButton", background=[("active", LINE)])
        s.configure("Fox.Horizontal.TScale", background=BG, troughcolor=PANEL)
        s.configure("Fox.Horizontal.TProgressbar", background=CRIMSON, troughcolor=PANEL,
                    bordercolor=LINE, lightcolor=CRIMSON, darkcolor=CRIMSON)

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 6))
        tt = tk.Frame(top, bg=BG)
        tt.pack(side="left")
        self.title_var = tk.StringVar(value="DREADFOX")
        tk.Label(tt, textvariable=self.title_var, bg=BG, fg=TXT, font=(UI[0], 17, "bold")).pack(anchor="w")
        self.sub_var = tk.StringVar()
        tk.Label(tt, textvariable=self.sub_var, bg=BG, fg=CRIMSON, font=(UI[0], 8, "bold")).pack(anchor="w")

        self.stats = tk.Frame(top, bg=BG)
        self.stats.pack(side="right")
        self.stat_vars = {}
        for key, label in (("level", "LEVEL"), ("tails", "TAILS"), ("mood", "MOOD")):
            f = tk.Frame(self.stats, bg=PANEL, padx=8, pady=6, highlightthickness=1, highlightbackground=LINE)
            f.pack(side="left", padx=3)
            v = tk.StringVar(value="–")
            tk.Label(f, textvariable=v, bg=PANEL, fg=HOT if key != "level" else TXT,
                     font=(UI[0], 14 if key != "mood" else 9, "bold"),
                     width=11 if key == "mood" else 0).pack()
            tk.Label(f, text=label, bg=PANEL, fg=DIM, font=(UI[0], 7, "bold")).pack()
            self.stat_vars[key] = v

        self.stage_var = tk.StringVar()
        tk.Label(self, textvariable=self.stage_var, bg=BG, fg=GOLD,
                 font=(UI[0], 11, "bold")).pack(anchor="w", padx=16, pady=(2, 0))

        self.canvas = tk.Canvas(self, width=400, height=360, bg=PANEL, highlightthickness=1,
                                highlightbackground=LINE, bd=0, cursor="hand2")
        self.canvas.pack(padx=14, pady=8, fill="x")
        self.canvas.bind("<Button-1>", self._boop)

        # Zenko memory pulse (aggregates only)
        self.zenko_var = tk.StringVar()
        tk.Label(self, textvariable=self.zenko_var, bg=BG, fg=STEEL, font=(MONO[0], 9)).pack(anchor="w", padx=16)

        # XP progress
        xpr = tk.Frame(self, bg=BG)
        xpr.pack(fill="x", padx=16, pady=(6, 0))
        tk.Label(xpr, text="EXPERIENCE", bg=BG, fg=DIM, font=(UI[0], 8, "bold")).pack(side="left")
        self.xp_txt = tk.StringVar()
        tk.Label(xpr, textvariable=self.xp_txt, bg=BG, fg=STEEL, font=MONO).pack(side="right")
        self.xp_bar = ttk.Progressbar(self, style="Fox.Horizontal.TProgressbar", maximum=100)
        self.xp_bar.pack(fill="x", padx=16, pady=(4, 2))
        self.next_txt = tk.StringVar()
        tk.Label(self, textvariable=self.next_txt, bg=BG, fg=DIM, font=(UI[0], 9),
                 wraplength=440, justify="left").pack(anchor="w", padx=16, pady=(2, 8))

        # controls: slider/evolve preview the stages; LIVE returns to the real fox
        ctl = tk.Frame(self, bg=BG)
        ctl.pack(fill="x", padx=14, pady=(2, 14))
        self.scale = ttk.Scale(ctl, from_=1, to=9, orient="horizontal", style="Fox.Horizontal.TScale",
                               command=self._slide)
        self.scale.pack(fill="x", pady=(0, 8))
        self._set_scale(self.stage_idx() + 1)
        row = tk.Frame(ctl, bg=BG)
        row.pack(fill="x")
        ttk.Button(row, text="●  LIVE", style="Fox.TButton", command=self._live).pack(side="left")
        ttk.Button(row, text="▶ PREVIEW EVOLUTION", style="Steel.TButton", command=self._evolve).pack(side="left", padx=6)
        ttk.Button(row, text="💬 TALK · ZENKO", style="Steel.TButton", command=self._talk).pack(side="right")
        self.chat = None

    def _set_scale(self, value):
        """Move the slider without it counting as the user choosing a preview stage."""
        self._syncing = True
        try:
            self.scale.set(value)
        finally:
            self._syncing = False

    # --- drawing -------------------------------------------------------------
    FOX_SCALE, FOX_X, FOX_Y = 1.55, 45, 46   # the design's 200x200 viewBox inside the 400x360 canvas

    def render(self):
        c = self.canvas
        c.delete("all")
        idx, mood = self.stage_idx(), self.mood()
        name = STAGES[idx][0]
        art_mood = STAGE_VOICE.get(mood, mood)          # preview stage moods use the live faces
        sway, fire_mult, flicker = LOOK.get(art_mood, DEFAULT_LOOK)
        now, t = time.time(), self.t
        hop = 0.0
        if self.hop_until > now:
            hop = math.sin((1 - (self.hop_until - now) / 0.5) * math.pi) * 16
        breathe = math.sin(t * 1.6) * 1.2

        # foxfire brightness: recent success (from FoxWorx) x mood
        level = float((self.data or {}).get("foxfire", 0.7)) if self.preview is None else 0.85
        level = max(0.25, min(1.0, level * fire_mult))
        blink = self.blink_until > now and fox_art.MOOD_ART.get(art_mood, {}).get("eye") != "closed"
        if now > self.next_blink:
            self.blink_until, self.next_blink = now + 0.14, now + random.uniform(3.5, 7.0)

        s = self.FOX_SCALE
        cx, cy = self.FOX_X + 100 * s, self.FOX_Y + 112 * s
        # milestone / evolution flare: expanding gold rings behind the fox
        if self.flare_until > now:
            p = 1 - (self.flare_until - now) / 2.5
            for k in range(3):
                r = 60 + (p * 130 + k * 22)
                c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=blend(GOLD, PANEL, min(1, p + k * 0.2)), width=3)
        shapes = fox_art.build_fox(idx + 1, art_mood, t=t, sway=sway, fire=level, flicker=flicker, blink=blink)
        fox_art.draw(c, shapes, self.FOX_X, self.FOX_Y - hop + breathe, s)

        self._speech(mood, idx, now)
        self._readout(idx, mood, name)

    def _speech(self, mood, idx, now):
        if self.preview is None and self.shot_mood is None:
            if mood != self.shown_mood:          # mood shifts are announced right away...
                if self.shown_mood is not None:
                    if self.flare_until > now:   # ...unless a milestone line is showing: queue it
                        self.next_chat = self.bubble_until + 1
                    else:
                        self._react(line_for(mood, idx))
                self.shown_mood = mood
            elif now >= self.next_chat:
                if self.chat is not None and self.chat.winfo_exists() and self.chat._run_active():
                    self.next_chat = now + SPEAK_EVERY     # no idle chatter over a live run's narration
                else:
                    self._react(line_for(mood, idx))
        elif self.shot_mood is not None and not self.bubble:
            self.bubble, self.bubble_until = line_for(mood, idx, random.Random(idx)), now + 7
        if not self.bubble or now > self.bubble_until:
            return
        c = self.canvas
        tid = c.create_text(200, 22, text=self.bubble, fill=TXT, font=(UI[0], 9, "italic"), width=340)
        x0, y0, x1, y1 = c.bbox(tid)
        c.create_rectangle(x0 - 10, y0 - 6, x1 + 10, y1 + 6, fill=PANEL2, outline=blend(GOLD, PANEL, 0.4))
        c.create_polygon(196, y1 + 6, 204, y1 + 6, 200, y1 + 14, fill=PANEL2, outline="")
        c.tag_raise(tid)

    def _readout(self, idx, mood, name):
        d = self.data or {}
        live = self.preview is None and self.shot_mood is None
        nxt = STAGES[idx + 1] if idx + 1 < len(STAGES) else None
        self.stat_vars["tails"].set(f"{idx + 1}/9")
        self.stat_vars["mood"].set(mood)
        if live and d.get("hatched"):
            pet = d.get("name") or "DreadFox"
            self.title_var.set(pet.upper()[:16])
            self.sub_var.set("COMPANION · LIVE FROM FOXWORX")
            self.stat_vars["level"].set(str(d.get("level", 1)))
            xp = int(d.get("xp", 0) or 0)
            nt = d.get("next_tail")
            self.stage_var.set(f"✦ {d.get('stage') or name}")
            if nt:
                self.xp_txt.set(f"{xp} / {nt['xp']} XP")
                self.xp_bar["value"] = min(100, xp / max(1, nt["xp"]) * 100)
                self.next_txt.set(f"Next evolution — {nt['tails']} tails at {nt['xp']} XP and "
                                  f"{nt['days']} days together (now day {d.get('age_days', 0)}).")
            else:
                self.xp_txt.set(f"{xp} XP · MAX")
                self.xp_bar["value"] = 100
                self.next_txt.set("All nine tails awakened. The guardian's vigil continues.")
            z = d.get("zenko") or {}
            rate = z.get("success_rate")
            self.zenko_var.set(
                f"ZENKO · {z.get('lessons', 0)} lessons · {z.get('validated', 0)} proven · "
                f"{'—' if rate is None else f'{round(rate * 100)}%'} success · "
                f"{z.get('fail_streak', 0)} misses")
            return
        self.title_var.set("DREADFOX")
        self.sub_var.set("COMPANION · PREVIEW" if self.preview is not None else "COMPANION · WAITING FOR FOXWORX")
        self.stat_vars["level"].set(str(idx + 1))
        self.stage_var.set(f"✦ {name}")
        self.xp_txt.set(f"{STAGES[idx][1]} / {nxt[1]} XP" if nxt else f"{STAGES[idx][1]} XP · MAX")
        self.xp_bar["value"] = (STAGES[idx][1] / nxt[1] * 100) if nxt else 100
        self.next_txt.set(f"Next evolution — {name} → {nxt[0]} at {nxt[1]} XP and {nxt[2]} days together."
                          if nxt else "All nine tails awakened. The guardian's vigil continues.")
        self.zenko_var.set("ZENKO · no live data yet — hatch the DreadFox in FoxWorx"
                           if self.preview is None else "ZENKO · preview — press LIVE for the real fox")

    # --- animation & controls -----------------------------------------------
    def _tick(self):
        self.t += FRAME_MS / 1000
        self.render()
        self.after(FRAME_MS, self._tick)

    def _talk(self):
        """Open (or raise) the chat window: the same DreadFox conversation as FoxWorx's sanctuary."""
        if self.chat is not None and self.chat.winfo_exists():
            self.chat.deiconify()
            self.chat.lift()
            return
        self.chat = ChatWindow(self)

    def fox_says(self, text, hop=True):
        """Show a line in the fox's speech bubble (used by the chat window)."""
        text = " ".join(text.split())
        self._react(text if len(text) <= 150 else text[:147].rstrip() + "…")
        if hop:
            self.hop_until = time.time() + 0.5

    def _boop(self, _event=None):
        self.hop_until = time.time() + 0.5
        self._react(line_for(self.mood(), self.stage_idx()))

    def _slide(self, val):
        if self._syncing:
            return
        self.preview = int(round(float(val))) - 1

    def _live(self):
        if self.auto:
            self.after_cancel(self.auto)
            self.auto = None
        self.preview = None
        self._set_scale(self.stage_idx() + 1)

    def _evolve(self):
        if self.auto:
            self.after_cancel(self.auto)
            self.auto = None
            return
        self.preview = -1
        self._step()

    def _step(self):
        self.preview = (self.preview + 1) % 9
        self._set_scale(self.preview + 1)
        self.auto = None if self.preview == 8 else self.after(1300, self._step)


# --- chat: the widget talks through FoxWorx's own pet-chat API --------------------------------
def _foxworx_url():
    """FoxWorx's address: DREADFOX_FOXWORX_URL, else the port in its settings file, else 8765."""
    if os.environ.get("DREADFOX_FOXWORX_URL"):
        return os.environ["DREADFOX_FOXWORX_URL"].rstrip("/")
    port = "8765"
    try:
        with open(os.path.expanduser("~/.config/foxworx-workbench/settings.env")) as fh:
            for line in fh:
                m = re.match(r'\s*FOXWORX_PORT\s*=\s*"?(\d+)"?\s*$', line)
                if m:
                    port = m.group(1)
    except OSError:
        pass
    return f"http://127.0.0.1:{port}"


class FoxWorxError(Exception):
    pass


class FoxWorxClient:
    """Minimal pet-chat client. Mutations carry the same Origin + CSRF proof the browser sends,
    so FoxWorx applies all of its usual checks. Works with FoxWorx's no-login (open) mode."""

    def __init__(self, base=None):
        self.base = base or _foxworx_url()
        self.csrf = None

    def _call(self, method, path, body=None, timeout=10):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if method != "GET":
            if self.csrf is None:
                self.csrf = self._call("GET", "/api/session")["csrf"]
            headers.update({"Origin": self.base, "X-FOXWORX-CSRF": self.csrf, "Content-Type": "application/json"})
            data = data or b"{}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - loopback FoxWorx only
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail")
            except (ValueError, AttributeError):
                detail = None
            if exc.code == 401:
                raise FoxWorxError("FoxWorx asks for a login. Widget chat works with FoxWorx's no-login mode; "
                                   "use the sanctuary in the browser instead.") from exc
            if exc.code == 403 and "Session check" in str(detail):
                self.csrf = None
            raise FoxWorxError(detail or f"FoxWorx answered HTTP {exc.code}.") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise FoxWorxError("FoxWorx isn't running. Start it with the FoxWorx desktop icon.") from exc

    def history(self):
        return self._call("GET", "/api/pet/chat")

    def send(self, message):
        # AI replies can take a while on a local model; FoxWorx itself gives up after 50 s.
        return self._call("POST", "/api/pet/chat", {"message": message}, timeout=70)

    def set_mode(self, mode):
        return self._call("PUT", "/api/pet/chat/mode", {"mode": mode})

    # -- operational modes: the FoxWorx AI copilot, proposals, jobs and Rabbit Hole runs --
    def engagements(self):
        return self._call("GET", "/api/engagements")

    def copilot_history(self, eid):
        return self._call("GET", "/api/chat?engagement_id=" + urllib.parse.quote(eid))

    def copilot(self, eid, message, mode):
        # FoxWorx allows a copilot turn up to 80 s (tool lookups included).
        return self._call("POST", "/api/chat", {"engagement_id": eid, "message": message, "mode": mode}, timeout=100)

    def approve(self, proposal):
        return self._call("POST", f"/api/proposals/{proposal['id']}/approve",
                          {"digest": proposal["digest"], "authorized": True}, timeout=30)

    def dismiss(self, proposal):
        return self._call("POST", f"/api/proposals/{proposal['id']}/dismiss")

    def job_output(self, jid, offset=0):
        return self._call("GET", f"/api/jobs/{jid}/output?offset={int(offset)}")

    def policy(self):
        return self._call("GET", "/api/adapter-policy")

    def rabbit_runs(self, eid):
        return self._call("GET", "/api/rabbit-runs?engagement_id=" + urllib.parse.quote(eid))

    def rabbit_start(self, spec):
        return self._call("POST", "/api/rabbit-runs", spec, timeout=30)

    def rabbit_get(self, rid):
        return self._call("GET", f"/api/rabbit-runs/{rid}")

    def rabbit_stop(self, rid):
        return self._call("POST", f"/api/rabbit-runs/{rid}/stop", timeout=30)

    def rabbit_message(self, rid, message):
        return self._call("POST", f"/api/rabbit-runs/{rid}/messages", {"message": message})


MODES = {
    "fox":     ("🦊 FOX", "Chat with your DreadFox. It can't see your work or run anything."),
    "chat":    ("💬 CHAT", "Ask Zenko anything about tools or the engagement. It answers and explains; it never runs commands."),
    "copilot": ("🛠 COPILOT", "Zenko proposes exact commands for your task. Nothing runs until you approve each one."),
    "rabbit":  ("↪ RABBIT HOLE", "Zenko runs installed tools on its own inside the target you authorize, adapting or switching "
                                 "tools when one fails, until it decides the task is solved or blocked."),
}
RUN_DONE = {"solved", "blocked", "stopped", "failed", "interrupted"}


class ChatWindow(tk.Toplevel):
    """Talk to the fox, or operate Zenko through FoxWorx: chat, copilot proposals, Rabbit Hole runs.
    Every request goes through FoxWorx's own API, so its scope checks, guardrails, approvals and
    audit log apply exactly as in the browser. Network calls run on a worker thread."""

    def __init__(self, companion):
        super().__init__(companion)
        self.companion = companion
        self.client = FoxWorxClient()
        self.results = queue.Queue()
        self.busy = False
        self.mode = "fox"
        self.engs = []
        self.run = None              # the Rabbit Hole run being followed
        self.run_seen = 0
        self.caps = {}
        self.title("DreadFox · Zenko")
        self.configure(bg=BG)
        self.geometry("540x700")
        self.minsize(440, 560)
        self._build()
        self.after(100, self._drain)
        self._switch("fox")
        self._run(self.client.engagements, self._got_engagements)
        self._run(self.client.policy, self._got_policy)

    # -- layout -------------------------------------------------------------------------
    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 4))
        self.title_var = tk.StringVar(value="DREADFOX · ZENKO")
        tk.Label(head, textvariable=self.title_var, bg=BG, fg=TXT, font=(UI[0], 13, "bold")).pack(side="left")
        self.mode_var = tk.StringVar(value="…")
        tk.Label(head, textvariable=self.mode_var, bg=PANEL, fg=GOLD, font=(UI[0], 8, "bold"),
                 padx=8, pady=3).pack(side="right")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=14, pady=(2, 4))
        self.mode_btns = {}
        for key, (label, _desc) in MODES.items():
            b = tk.Button(bar, text=label, command=lambda k=key: self._switch(k), bd=0, padx=8, pady=5,
                          font=(UI[0], 9, "bold"), activebackground=HOT, activeforeground="#fff", cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            self.mode_btns[key] = b
        self.desc_var = tk.StringVar()
        tk.Label(self, textvariable=self.desc_var, bg=BG, fg=STEEL, font=(UI[0], 9), wraplength=500,
                 justify="left").pack(anchor="w", padx=14)

        # engagement picker (operational modes)
        self.eng_row = tk.Frame(self, bg=BG)
        tk.Label(self.eng_row, text="ENGAGEMENT", bg=BG, fg=DIM, font=(UI[0], 8, "bold")).pack(side="left")
        self.eng_var = tk.StringVar()
        self.eng_box = ttk.Combobox(self.eng_row, textvariable=self.eng_var, state="readonly", width=28)
        self.eng_box.pack(side="left", padx=6)
        self.eng_box.bind("<<ComboboxSelected>>", lambda _e: self._reload())
        self.scope_var = tk.StringVar()
        tk.Label(self.eng_row, textvariable=self.scope_var, bg=BG, fg=DIM, font=(MONO[0], 8)).pack(side="left")

        # fox mode: AI toggle
        self.ai_var = tk.BooleanVar(value=False)
        self.ai_box = tk.Checkbutton(self, text="Use FoxWorx's selected AI model (e.g. LM Studio)",
                                     variable=self.ai_var, command=self._toggle_ai, bg=BG, fg=STEEL,
                                     selectcolor=PANEL, activebackground=BG, activeforeground=TXT,
                                     highlightthickness=0, font=(UI[0], 9))

        # rabbit hole: run grant
        self.grant = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=LINE, padx=10, pady=6)
        r1 = tk.Frame(self.grant, bg=PANEL)
        r1.pack(fill="x")
        tk.Label(r1, text="TARGET", bg=PANEL, fg=DIM, font=(UI[0], 8, "bold")).pack(side="left")
        self.target_var = tk.StringVar()
        tk.Entry(r1, textvariable=self.target_var, bg=PANEL2, fg=TXT, insertbackground=TXT, bd=0,
                 font=(MONO[0], 9), width=22).pack(side="left", padx=6)
        tk.Label(r1, text="STEPS", bg=PANEL, fg=DIM, font=(UI[0], 8, "bold")).pack(side="left", padx=(6, 0))
        self.steps_var = tk.StringVar(value="64")
        tk.Spinbox(r1, from_=1, to=10000, textvariable=self.steps_var, width=5, bg=PANEL2, fg=TXT,
                   buttonbackground=PANEL2, bd=0).pack(side="left", padx=4)
        tk.Label(r1, text="MIN", bg=PANEL, fg=DIM, font=(UI[0], 8, "bold")).pack(side="left", padx=(6, 0))
        self.mins_var = tk.StringVar(value="60")
        tk.Spinbox(r1, from_=1, to=1440, textvariable=self.mins_var, width=5, bg=PANEL2, fg=TXT,
                   buttonbackground=PANEL2, bd=0).pack(side="left", padx=4)
        self.auth_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self.grant, text="I'm authorized to test this target and let Zenko run installed tools on it "
                       "automatically for this run.", variable=self.auth_var, bg=PANEL, fg=STEEL,
                       selectcolor=PANEL2, activebackground=PANEL, activeforeground=TXT, highlightthickness=0,
                       font=(UI[0], 8), wraplength=480, justify="left").pack(anchor="w", pady=(4, 0))

        box = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        self.log_box = box
        self.log = tk.Text(box, height=8, bg=PANEL, fg=TXT, wrap="word", bd=0, padx=10, pady=8,
                           font=(UI[0], 10), state="disabled", cursor="arrow")
        sb = ttk.Scrollbar(box, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for tag, kw in {"fox": dict(foreground=GOLD, font=(UI[0], 9, "bold"), spacing1=8),
                        "you": dict(foreground=HOT, font=(UI[0], 9, "bold"), spacing1=8),
                        "text": dict(foreground=TXT, lmargin1=4, lmargin2=4),
                        "note": dict(foreground=DIM, font=(UI[0], 9, "italic"), spacing1=6),
                        "cmd": dict(foreground="#9ee8a8", background=BG, font=(MONO[0], 9), lmargin1=8,
                                    lmargin2=8, spacing1=4, spacing3=2),
                        "step": dict(foreground=STEEL, font=(MONO[0], 9), lmargin1=4),
                        "ok": dict(foreground=GREEN, font=(UI[0], 9, "bold"), spacing1=6),
                        "bad": dict(foreground=HOT, font=(UI[0], 9, "bold"), spacing1=6),
                        "out": dict(foreground=DIM, font=(MONO[0], 8), lmargin1=8, lmargin2=8)}.items():
            self.log.tag_configure(tag, **kw)

        self.entry = tk.Text(self, height=3, bg=PANEL2, fg=TXT, insertbackground=TXT, wrap="word", bd=0,
                             padx=8, pady=6, font=(UI[0], 10), highlightthickness=1, highlightbackground=LINE)
        self.entry.bind("<Return>", self._enter)
        self.row = tk.Frame(self, bg=BG)
        self.status = tk.StringVar(value="Enter sends · Shift+Enter for a new line")
        tk.Label(self.row, textvariable=self.status, bg=BG, fg=DIM, font=(UI[0], 8), wraplength=330,
                 justify="left").pack(side="left")
        self.send_btn = ttk.Button(self.row, text="SEND", style="Fox.TButton", command=self._send)
        self.send_btn.pack(side="right")
        self.stop_btn = ttk.Button(self.row, text="■ STOP RUN", style="Steel.TButton", command=self._stop_run)
        self.note_var = tk.StringVar()
        self.note = tk.Label(self, textvariable=self.note_var, bg=BG, fg=DIM, font=(UI[0], 8), wraplength=500,
                             justify="left")

    def _layout(self):
        """Show the controls the current mode needs (pack order = screen order)."""
        for w in (self.eng_row, self.ai_box, self.grant, self.log_box, self.entry, self.row, self.note):
            w.pack_forget()
        if self.mode == "fox":
            self.ai_box.pack(anchor="w", padx=10)
        else:
            self.eng_row.pack(fill="x", padx=14, pady=(6, 2))
        if self.mode == "rabbit" and not self._run_active():
            self.grant.pack(fill="x", padx=14, pady=(4, 2))
        self.log_box.pack(fill="both", expand=True, padx=14, pady=6)
        self.entry.pack(fill="x", padx=14)
        self.row.pack(fill="x", padx=14, pady=(6, 4))
        self.note.pack(anchor="w", padx=14, pady=(0, 10))
        if self.mode == "rabbit" and self._run_active():
            self.stop_btn.pack(side="right", padx=6)
        else:
            self.stop_btn.pack_forget()
        self.send_btn.configure(text="START RUN" if self.mode == "rabbit" and not self._run_active() else
                                "GUIDE RUN" if self.mode == "rabbit" else "SEND")

    def _switch(self, mode):
        if self.busy:
            return
        self.mode = mode
        for key, b in self.mode_btns.items():
            b.configure(bg=CRIMSON if key == mode else PANEL2, fg="#fff" if key == mode else STEEL)
        self.desc_var.set(MODES[mode][1])
        self.note_var.set({
            "fox": "Never paste targets, commands, credentials or findings. The fox can't run tools.",
            "chat": "Your message, the engagement scope and matching tool notes go to FoxWorx's selected model.",
            "copilot": "Review every command before approving. Approved commands run as your user; scope checks are not a sandbox.",
            "rabbit": "Runs only installed non-interactive CLI tools, inside the run's target and FoxWorx's guardrails; "
                      "every command is audited. You can steer it or stop it at any time.",
        }[mode])
        self.entry.delete("1.0", "end")
        self._layout()
        self._reload()
        self.entry.focus_set()

    # -- worker plumbing: call fn on a thread, hand the result to done() on the Tk thread --
    def _run(self, fn, done, *args):
        def work():
            try:
                self.results.put((done, fn(*args), None))
            except FoxWorxError as exc:
                self.results.put((done, None, str(exc)))
            except Exception as exc:  # keep the window alive on anything unexpected
                self.results.put((done, None, f"Unexpected error: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def _drain(self):
        try:
            while True:
                done, result, error = self.results.get_nowait()
                done(result, error)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain)

    # -- log rendering ------------------------------------------------------------------
    def _clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _write(self, text, tag="text"):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def _line(self, who, text):
        if who in ("fox", "you"):
            name = "DREADFOX" if self.mode == "fox" else "ZENKO"
            self._write((name if who == "fox" else "YOU") + "\n", who)
            self._write(text + "\n", "text")
        else:
            self._write(text + "\n", who)

    def _buttons(self, *specs):
        """Embed a row of buttons in the log: specs are (label, style, command)."""
        self.log.configure(state="normal")
        holder = tk.Frame(self.log, bg=PANEL)
        for label, style, cmd in specs:
            ttk.Button(holder, text=label, style=style, command=cmd).pack(side="left", padx=(8, 0))
        self.log.window_create("end", window=holder)
        self.log.insert("end", "\n")
        self.log.configure(state="disabled")
        self.log.see("end")
        return holder

    # -- loading ------------------------------------------------------------------------
    def _eid(self):
        name = self.eng_var.get()
        return next((e["id"] for e in self.engs if e.get("name") == name), None)

    def _eng(self):
        eid = self._eid()
        return next((e for e in self.engs if e["id"] == eid), None)

    def _got_engagements(self, data, error):
        if error:
            self.engs = []
            if self.mode != "fox":
                self._line("note", error)
            return
        self.engs = data or []
        self.eng_box["values"] = [e.get("name", e["id"]) for e in self.engs]
        if self.engs and not self.eng_var.get():
            self.eng_var.set(self.engs[0].get("name", self.engs[0]["id"]))
        if self.mode != "fox":
            self._reload()

    def _got_policy(self, data, error):
        if not error and data:
            self.caps = data
            self.steps_var.set(str(data.get("max_steps") or 64))
            self.mins_var.set(str(data.get("max_minutes") or 60))

    def _reload(self):
        self._clear()
        eng = self._eng()
        self.scope_var.set(("scope: " + ", ".join(eng.get("include", [])[:3])) if eng else "")
        if eng and not self.target_var.get() and eng.get("include"):
            self.target_var.set(eng["include"][0])
        if self.mode == "fox":
            self.mode_var.set("…")
            self._run(self.client.history, self._show_pet_history)
            return
        if not self.engs:
            self._line("note", "No engagement yet. Create one in FoxWorx (Scope & notes) so Zenko knows what is in scope.")
            self.mode_var.set("NO ENGAGEMENT")
            return
        if not eng:
            return
        if self.mode in ("chat", "copilot"):
            self.mode_var.set("TUTOR" if self.mode == "chat" else "COPILOT")
            self._run(self.client.copilot_history, self._show_copilot_history, eng["id"])
        else:
            self.mode_var.set("RABBIT HOLE")
            if self.caps and self.caps.get("status") != "active":
                self._line("note", "Rabbit Hole is disabled: no active administrator policy in /etc/foxworx.")
            self._run(self.client.rabbit_runs, self._show_runs, eng["id"])

    # -- fox mode -----------------------------------------------------------------------
    def _set_fox_mode(self, mode, model=""):
        self.ai_var.set(mode == "ai")
        self.mode_var.set(("AI · " + model) if mode == "ai" and model else
                          "AI MODE" if mode == "ai" else "OFFLINE · built-in replies")

    def _show_pet_history(self, data, error):
        if self.mode != "fox":
            return
        if error:
            self._line("note", error)
            self.mode_var.set("NOT CONNECTED")
            return
        if not data.get("hatched"):
            self._line("note", "Hatch your DreadFox in FoxWorx first (the Companion tab), then come back to talk.")
            self.mode_var.set("NOT HATCHED")
            return
        last_model = next((m.get("model") for m in reversed(data["messages"]) if m.get("mode") == "ai"), "")
        self._set_fox_mode(data.get("mode", "offline"), last_model)
        if not data["messages"]:
            self._line("note", "Say hello to your fox. Ask about its mood, its tails or its memories.")
        for m in data["messages"]:
            self._line("fox" if m["role"] == "assistant" else "you", m["text"])

    def _toggle_ai(self):
        mode = "ai" if self.ai_var.get() else "offline"
        self.status.set("Switching…")
        self._run(self.client.set_mode, self._mode_changed, mode)

    def _mode_changed(self, data, error):
        if error:
            self.ai_var.set(not self.ai_var.get())
            self.status.set(error)
            return
        self._set_fox_mode(data.get("mode", "offline"))
        self.status.set("AI replies come from FoxWorx's selected model." if data.get("mode") == "ai"
                        else "Offline replies are built in: free, instant, no AI calls.")

    # -- chat / copilot -----------------------------------------------------------------
    def _show_copilot_history(self, data, error):
        if self.mode not in ("chat", "copilot"):
            return
        if error:
            self._line("note", error)
            return
        if not data:
            self._line("note", "Describe your task, e.g. \"Find the web services on the lab host and what software they run.\""
                       if self.mode == "copilot" else "Ask about a tool, an option, or how to read some output.")
        for m in data[-30:]:
            self._line("fox" if m["role"] == "assistant" else "you", m["text"])

    def _copilot_reply(self, data, error):
        self._done()
        if error:
            self._line("note", error)
            self.companion.fox_says("*tilts head* …that didn't go through.", hop=False)
            return
        self._line("fox", data["answer"])
        self.companion.fox_says(data["answer"])
        for prop in data.get("proposals") or []:
            self._proposal(prop)

    def _proposal(self, prop):
        self._write("PROPOSED COMMAND\n", "fox")
        self._write(prop.get("command", "") + "\n", "cmd")
        self._write(f"target {prop.get('target', '?')} · {prop.get('profile', '')} · {prop.get('scope_check', '')}"
                    + (f" · {prop['reason']}" if prop.get("reason") else "") + "\n", "step")
        guard = prop.get("guardrail_findings") or []
        for g in guard:
            self._write("⚠ Guardrail: " + g + "\n", "bad")
        holder = None

        def approve():
            warn = ("\n\nGuardrail warnings:\n• " + "\n• ".join(guard)) if guard else ""
            if not messagebox.askyesno("Run this command?", "Zenko proposed:\n\n" + prop.get("command", "") +
                                       f"\n\nTarget: {prop.get('target', '?')}" + warn +
                                       "\n\nRun it now as your user?", parent=self,
                                       icon="warning" if guard else "question"):
                return
            for child in holder.winfo_children():
                child.state(["disabled"])
            self._line("note", "Approved. Running…")
            self.companion.fox_says("*ears perk* Running it now!", hop=True)
            self._run(self.client.approve, self._job_started, prop)

        def dismiss():
            for child in holder.winfo_children():
                child.state(["disabled"])
            self._run(self.client.dismiss, lambda _d, e: self._line("note", e or "Dismissed."), prop)

        holder = self._buttons(("✔ APPROVE & RUN", "Fox.TButton", approve), ("✕ DISMISS", "Steel.TButton", dismiss))

    def _job_started(self, job, error):
        if error:
            self._line("bad", "Not run: " + error)
            return
        self._follow_job(job["id"], 0, "")

    def _follow_job(self, jid, offset, text):
        def got(data, error):
            if error:
                self._line("note", error)
                return
            nonlocal_text = text + data.get("text", "")
            job = data.get("job") or {}
            if job.get("status") in ("queued", "running", "stopping"):
                self.after(1500, lambda: self._follow_job(jid, data.get("offset", offset), nonlocal_text[-20000:]))
                return
            ok = job.get("status") == "succeeded"
            self._line("ok" if ok else "bad", f"JOB {job.get('status', '?').upper()} (exit {job.get('returncode')})")
            tail = "\n".join(nonlocal_text.strip().splitlines()[-18:])
            if tail:
                self._write(tail + "\n", "out")
            self.companion.fox_says("Done! Take a look at the output." if ok else "*ears flatten* That one failed.")
        self._run(self.client.job_output, got, jid, offset)

    # -- rabbit hole --------------------------------------------------------------------
    def _run_active(self):
        return bool(self.run and self.run.get("status") not in RUN_DONE)

    def _show_runs(self, data, error):
        if self.mode != "rabbit":
            return
        if error:
            self._line("note", error)
            return
        live = next((r for r in (data or []) if r.get("status") in ("queued", "running", "stopping")), None)
        if live:
            self.run, self.run_seen = live, 0
            self._layout()
            self._render_run(live)
            self._poll_run()
        else:
            self._line("note", "Describe the objective below, check the target and budget, tick the authorization box, "
                               "then START RUN. Zenko keeps adapting until it decides the task is solved or blocked; "
                               "the budget is only the ceiling.")

    def _start_run(self, objective):
        eng = self._eng()
        try:
            steps, mins = int(self.steps_var.get()), int(self.mins_var.get())
        except ValueError:
            self.status.set("Steps and minutes must be whole numbers.")
            return False
        if not self.auth_var.get():
            self.status.set("Tick the authorization box first.")
            return False
        if not self.target_var.get().strip():
            self.status.set("Enter the exact target (it must be inside the engagement scope).")
            return False
        spec = {"engagement_id": eng["id"], "objective": objective, "target": self.target_var.get().strip(),
                "execution_mode": "installed_cli", "profiles": [], "max_steps": steps, "max_minutes": mins,
                "authorized": True}
        self._line("you", objective)
        self.companion.fox_says("*fluffs up* Into the rabbit hole we go!", hop=True)
        self._run(self.client.rabbit_start, self._run_started, spec)
        return True

    def _run_started(self, record, error):
        self._done()
        if error:
            self._line("bad", "Run not started: " + error)
            return
        self.auth_var.set(False)          # a grant covers exactly one run
        self.run, self.run_seen = record, 0
        self._layout()
        self._render_run(record)
        self._poll_run()

    def _poll_run(self):
        if not self._run_active() or self.mode != "rabbit" or not self.winfo_exists():
            return
        rid = self.run["id"]

        def got(record, error):
            if error:
                self.status.set(error)
            elif record and self.run and record["id"] == self.run["id"]:
                self.run = record
                self._render_run(record)
                if not self._run_active():
                    self._layout()
                    return
            self.after(2000, self._poll_run)
        self._run(self.client.rabbit_get, got, rid)

    def _render_run(self, r):
        """Print what changed since the last poll: the header once, then each step whenever its status moves."""
        if self.run_seen == 0:
            self._write(f"RUN {r['id'][:8]} · target {r.get('target')} · up to {r.get('max_steps')} steps / "
                        f"{r.get('max_minutes')} min\n", "fox")
            self.run_seen = 1
            self.step_status = {}
        steps = r.get("steps") or []
        for st in steps:
            key, status = st.get("number"), st.get("status")
            if self.step_status.get(key) == status:
                continue
            self.step_status[key] = status
            name = st.get("tool") or st.get("profile")
            line = f"#{key} {name} → {status}"
            if st.get("returncode") is not None:
                line += f" (exit {st['returncode']})"
            if st.get("reason") and status == "running":
                line += f" · {st['reason']}"
            if st.get("detail"):
                line += f" · {st['detail']}"
            self._write(line + "\n", "step")
            self.companion.fox_says(
                f"Trying {name}…" if status == "running" else
                "Got something useful!" if status == "succeeded" else
                f"Learning about {name}…" if status == "learned" else
                "*ears back* Blocked. Not ours to touch." if status == "blocked" else
                "That didn't work. Adjusting…", hop=status == "succeeded")
        used = r.get("decisions_used", len(steps))
        self.status.set(f"{r.get('status', '?').upper()} · {used}/{r.get('max_steps')} decisions")
        if r.get("status") in RUN_DONE:
            solved = r["status"] == "solved"
            self._line("ok" if solved else "bad", f"RUN {r['status'].upper()}")
            if r.get("summary"):
                self._write(r["summary"] + "\n", "text")
            if r.get("next_steps"):
                self._write("Next: " + r["next_steps"] + "\n", "note")
            self.companion.fox_says("Task complete! *happy tail swish*" if solved else
                                    "I can't finish this with the tools I have. Here's what I found.")

    def _stop_run(self):
        if self._run_active():
            self.status.set("Stopping…")
            self._run(self.client.rabbit_stop, lambda rec, e: self.status.set(e or "Stop requested."), self.run["id"])

    # -- sending ------------------------------------------------------------------------
    def _enter(self, event):
        if event.state & 0x1:      # Shift+Enter keeps the newline
            return None
        self._send()
        return "break"

    def _done(self):
        self.busy = False
        self.send_btn.state(["!disabled"])
        if self.status.get().startswith(("Zenko is", "DreadFox is")):
            self.status.set("Enter sends · Shift+Enter for a new line")

    def _send(self):
        text = self.entry.get("1.0", "end").strip()
        if not text or self.busy:
            return
        if self.mode != "fox" and not self._eid():
            self.status.set("Choose an engagement first.")
            return
        if self.mode == "rabbit":
            if self._run_active():
                self.entry.delete("1.0", "end")
                self._line("you", text)
                self._run(self.client.rabbit_message, lambda _d, e: self.status.set(e or "Guidance sent to the run."),
                          self.run["id"], text)
                return
            if len(text) < 8:
                self.status.set("Describe the objective in a little more detail.")
                return
            self.busy = True
            self.send_btn.state(["disabled"])
            if not self._start_run(text):
                self._done()
                return
            self.entry.delete("1.0", "end")
            return
        self.busy = True
        self.send_btn.state(["disabled"])
        self.entry.delete("1.0", "end")
        self._line("you", text)
        if self.mode == "fox":
            self.status.set("DreadFox is thinking…" + (" (local models can take ~30 s)" if self.ai_var.get() else ""))
            self.companion.fox_says("*thinking…*", hop=False)
            self._run(self.client.send, self._fox_replied, text)
        else:
            self.status.set("Zenko is thinking… (local models can take up to a minute)")
            self.companion.fox_says("*thinking…*", hop=False)
            self._run(self.client.copilot, self._copilot_reply, self._eid(), text,
                      "tutor" if self.mode == "chat" else "copilot")

    def _fox_replied(self, data, error):
        self._done()
        if error:
            self._line("note", error)
            self.status.set("Not sent. Nothing was saved.")
            self.companion.fox_says("*tilts head* …I couldn't hear that.", hop=False)
            return
        self._line("fox", data["answer"])
        if data.get("mode") == "ai":
            self._set_fox_mode("ai", data.get("model", ""))
        self.companion.fox_says(data["answer"])


def _shot(stage, out, mood=None):
    """Headless render of one stage (and optional mood) to PNG (canvas PostScript → ghostscript)."""
    app = Companion(shot=stage - 1, shot_mood=mood)
    app.t = 0.6
    app.render()
    app.update()
    app.update_idletasks()
    ps = out + ".ps"
    app.canvas.postscript(file=ps, colormode="color", width=402, height=362)
    os.system(f"gs -q -dSAFER -dBATCH -dNOPAUSE -sDEVICE=pngalpha -r144 "
              f"-sOutputFile={out} {ps} >/dev/null 2>&1")
    os.remove(ps)
    app.destroy()


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--shot":
        _shot(int(sys.argv[2]), sys.argv[3], sys.argv[4] if len(sys.argv) >= 5 else None)
    else:
        Companion().mainloop()
