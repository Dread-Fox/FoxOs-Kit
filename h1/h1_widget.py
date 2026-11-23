#!/usr/bin/env python3
"""DreadFox Bounty Radar — FoxOS desktop widget for the local HackerOne intel DB.

Reads ~/h1/data/h1.db (filled by `h1 sync` / the h1-sync systemd timer) and live-reloads
whenever a sync lands. Can trigger a sync itself, export targets, and scaffold reports.
"""
import json
import re
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import h1lib as L  # noqa: E402

H1 = os.path.join(os.path.dirname(os.path.realpath(__file__)), "h1.py")
BADGE = os.path.expanduser("~/foxos/assets/badge.png")

BG, PANEL, PANEL2, LINE = "#0c0e12", "#151920", "#1c222b", "#2a313c"
TXT, DIM, STEEL = "#e6e9ee", "#8a94a3", "#b8c0cc"
CRIMSON, HOT, GOLD, GREEN, BLUE = "#E11D2A", "#FF2E4D", "#e0a526", "#4fb36b", "#6fa8dc"
MONO = ("Fira Code", 10)
UI = ("DejaVu Sans", 10)
SEV_COL = {"critical": HOT, "high": GOLD, "medium": BLUE, "low": DIM, "none": DIM}
RELOAD_MS = 20_000
AUTOSYNC_MIN = 30


def bar(score, width=10):
    n = max(0, min(width, round((score - 40) / 60 * width)))  # 40..100 spread for contrast
    return "█" * n + "░" * (width - n)


class Radar(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Bounty Radar — DreadFox")
        self.geometry("1480x860")
        self.minsize(980, 600)
        self.configure(bg=BG)
        try:
            self.badge = tk.PhotoImage(file=BADGE)
            self.iconphoto(True, self.badge)
            f = max(1, self.badge.width() // 44)
            self.badge_small = self.badge.subsample(f, f)
        except tk.TclError:
            self.badge_small = None
        self.syncing = False
        self.last_seen_sync = None
        self.rows = {}
        self.cur = None
        self._style()
        self._build()
        self.reload()
        self.after(RELOAD_MS, self.tick)
        self.after(500, self.poll_progress)

    # ------------------------------------------------------------------ styling
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TXT, fieldbackground=PANEL, font=UI, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, troughcolor=PANEL)
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TXT, rowheight=24,
                    font=MONO, borderwidth=0)
        s.map("Treeview", background=[("selected", "#3a0d12")], foreground=[("selected", "#ffffff")])
        s.configure("Treeview.Heading", background=PANEL2, foreground=STEEL, font=(UI[0], 9, "bold"),
                    relief="flat", borderwidth=0)
        s.map("Treeview.Heading", background=[("active", LINE)])
        s.configure("TCheckbutton", background=BG, foreground=STEEL, indicatorbackground=PANEL,
                    indicatorforeground=HOT)
        s.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("selected", CRIMSON)])
        s.configure("Fox.TButton", background=CRIMSON, foreground="#fff", borderwidth=0, padding=(12, 5),
                    font=(UI[0], 9, "bold"))
        s.map("Fox.TButton", background=[("active", HOT), ("disabled", LINE)])
        s.configure("Steel.TButton", background=PANEL2, foreground=STEEL, borderwidth=0, padding=(10, 5))
        s.map("Steel.TButton", background=[("active", LINE)])
        s.configure("TEntry", fieldbackground=PANEL, foreground=TXT, insertcolor=HOT, padding=5)
        s.configure("Vertical.TScrollbar", background=PANEL2, arrowcolor=DIM, troughcolor=PANEL)
        s.configure("TPanedwindow", background=BG)
        s.configure("Fox.Horizontal.TProgressbar", background=CRIMSON, troughcolor=PANEL, bordercolor=LINE,
                    lightcolor=HOT, darkcolor=CRIMSON, thickness=10)

    # ------------------------------------------------------------------- layout
    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 6))
        if self.badge_small:
            tk.Label(top, image=self.badge_small, bg=BG).pack(side="left", padx=(0, 10))
        tt = tk.Frame(top, bg=BG)
        tt.pack(side="left")
        tk.Label(tt, text="BOUNTY RADAR", bg=BG, fg=TXT, font=(UI[0], 17, "bold")).pack(anchor="w")
        tk.Label(tt, text="DREADFOX · HACKERONE TARGET LINEUP", bg=BG, fg=CRIMSON,
                 font=(UI[0], 8, "bold")).pack(anchor="w")
        self.stats = tk.Frame(top, bg=BG)
        self.stats.pack(side="right")
        self.stat_vars = {}
        for key, label in (("open", "OPEN BOUNTY"), ("fresh", "FRESH ≤60D"), ("assets", "NEW ASSETS 30D"),
                           ("changes", "CHANGES 24H")):
            f = tk.Frame(self.stats, bg=PANEL, padx=14, pady=6, highlightthickness=1, highlightbackground=LINE)
            f.pack(side="left", padx=4)
            v = tk.StringVar(value="–")
            tk.Label(f, textvariable=v, bg=PANEL, fg=HOT if key != "open" else TXT,
                     font=(UI[0], 15, "bold")).pack()
            tk.Label(f, text=label, bg=PANEL, fg=DIM, font=(UI[0], 7, "bold")).pack()
            self.stat_vars[key] = v

        bar_ = tk.Frame(self, bg=BG)
        bar_.pack(fill="x", padx=14, pady=4)
        tk.Label(bar_, text="⌕", bg=BG, fg=DIM, font=(UI[0], 13)).pack(side="left")
        self.q = tk.StringVar()
        e = ttk.Entry(bar_, textvariable=self.q, width=34)
        e.pack(side="left", padx=(4, 12))
        e.bind("<KeyRelease>", lambda _: self.reload(keep=True))
        self.f_bounty, self.f_open, self.f_fresh = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar()
        for var, txt in ((self.f_bounty, "bounty only"), (self.f_open, "open only"),
                         (self.f_fresh, "fresh / new assets only")):
            ttk.Checkbutton(bar_, text=txt, variable=var, command=lambda: self.reload(keep=True)).pack(side="left", padx=6)
        self.sync_btn = ttk.Button(bar_, text="⟳  SYNC NOW", style="Fox.TButton", command=self.sync)
        self.sync_btn.pack(side="right")
        self.status = tk.StringVar()
        tk.Label(bar_, textvariable=self.status, bg=BG, fg=DIM, font=(UI[0], 9)).pack(side="right", padx=12)

        # sync progress row: hidden unless a sync (timer, widget or shell) is running
        self.prog = tk.Frame(self, bg=BG)
        tk.Label(self.prog, text="SYNC", bg=BG, fg=CRIMSON, font=(UI[0], 8, "bold")).pack(side="left")
        self.prog_txt = tk.StringVar()
        tk.Label(self.prog, textvariable=self.prog_txt, bg=BG, fg=STEEL, font=(UI[0], 9), width=44,
                 anchor="w").pack(side="left", padx=(8, 8))
        self.prog_pct = tk.StringVar()
        tk.Label(self.prog, textvariable=self.prog_pct, bg=BG, fg=TXT, font=(UI[0], 9, "bold"),
                 width=6, anchor="e").pack(side="right")
        self.prog_bar = ttk.Progressbar(self.prog, style="Fox.Horizontal.TProgressbar", maximum=100)
        self.prog_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.prog_shown = False
        self.prog_mode = None
        self._svc_cache = (0.0, False)

        pw = ttk.Panedwindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=14, pady=6)
        self.pw = pw

        left = tk.Frame(pw, bg=BG)
        cols = ("rank", "score", "bar", "program", "web", "wild", "sev", "paid", "median", "flags")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        spec = {"rank": ("#", 36, "e"), "score": ("SCORE", 56, "e"), "bar": ("", 100, "w"),
                "program": ("PROGRAM", 190, "w"), "web": ("WEB", 46, "e"), "wild": ("*.", 36, "e"),
                "sev": ("MAX SEV", 80, "w"), "paid": ("PAID", 46, "e"), "median": ("MEDIAN", 74, "e"),
                "flags": ("FLAGS", 190, "w")}
        for c in cols:
            t, w, an = spec[c]
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=an, stretch=c in ("program", "flags"))
        for tag, col in (("hot", HOT), ("fresh", GREEN), ("dim", DIM), ("gold", GOLD)):
            self.tree.tag_configure(tag, foreground=col)
        self.tree.tag_configure("odd", background="#12161c")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.select())
        self.tree.bind("<Double-1>", lambda _: self.open_h1())
        pw.add(left, weight=3)

        right = tk.Frame(pw, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        self.d_title = tk.Label(right, text="Select a program", bg=PANEL, fg=TXT, font=(UI[0], 14, "bold"),
                                anchor="w", wraplength=460, justify="left")
        self.d_title.pack(fill="x", padx=12, pady=(10, 0))
        self.d_sub = tk.Label(right, text="", bg=PANEL, fg=DIM, font=(UI[0], 9), anchor="w")
        self.d_sub.pack(fill="x", padx=12)
        self.d_why = tk.Text(right, height=8, bg=PANEL, fg=STEEL, font=(UI[0], 9), relief="flat", wrap="word",
                             highlightthickness=0, padx=12, pady=6)
        self.d_why.tag_configure("plus", foreground=GREEN)
        self.d_why.tag_configure("warn", foreground=GOLD)
        self.d_why.pack(fill="x")
        tk.Label(right, text="IN-SCOPE ASSETS  (★ bounty · select one for a report)", bg=PANEL, fg=CRIMSON,
                 font=(UI[0], 8, "bold"), anchor="w").pack(fill="x", padx=12, pady=(4, 2))
        sf = tk.Frame(right, bg=PANEL)
        sf.pack(fill="both", expand=True, padx=8)
        self.scopes = ttk.Treeview(sf, columns=("b", "type", "sev", "asset"), show="headings", selectmode="browse")
        for c, t, w in (("b", "", 22), ("type", "TYPE", 96), ("sev", "SEV", 80), ("asset", "ASSET", 260)):
            self.scopes.heading(c, text=t)
            self.scopes.column(c, width=w, anchor="w", stretch=c == "asset")
        self.scopes.tag_configure("new", foreground=GREEN)
        self.scopes.tag_configure("nob", foreground=DIM)
        ssb = ttk.Scrollbar(sf, orient="vertical", command=self.scopes.yview)
        self.scopes.configure(yscrollcommand=ssb.set)
        self.scopes.pack(side="left", fill="both", expand=True)
        ssb.pack(side="left", fill="y")
        btns = tk.Frame(right, bg=PANEL)
        btns.pack(fill="x", padx=10, pady=10)
        for txt, cmd, st in (("Open on H1", self.open_h1, "Steel.TButton"),
                             ("Export targets", self.export, "Steel.TButton"),
                             ("Copy hosts", self.copy_hosts, "Steel.TButton"),
                             ("Recon", self.open_recon, "Steel.TButton"),
                             ("Hunt leads", self.open_leads, "Steel.TButton"),
                             ("New report", self.new_report, "Fox.TButton")):
            ttk.Button(btns, text=txt, style=st, command=cmd).pack(side="left", padx=3)
        pw.add(right, weight=2)

        feed = tk.Frame(self, bg=BG)
        feed.pack(fill="x", padx=14, pady=(0, 12))
        tk.Label(feed, text="LIVE CHANGE FEED", bg=BG, fg=CRIMSON, font=(UI[0], 8, "bold")).pack(anchor="w")
        self.feed = tk.Listbox(feed, height=6, bg=PANEL, fg=STEEL, font=(MONO[0], 9), relief="flat",
                               highlightthickness=1, highlightbackground=LINE, selectbackground="#3a0d12",
                               activestyle="none")
        self.feed.pack(fill="x")
        self.feed.bind("<Double-1>", self.feed_jump)
        self.feed_handles = []

    # --------------------------------------------------------------------- data
    def reload(self, keep=False):
        try:
            c = L.db()
            rows = L.ranked(c, bounty_only=self.f_bounty.get(), open_only=self.f_open.get(),
                            query=self.q.get().strip() or None)
            last = L.meta_get(c, "last_sync")
        except Exception as e:  # noqa: BLE001
            self.status.set(f"db error: {e}")
            return
        self.last_seen_sync = last
        sel = self.cur
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        shown = 0
        for r in rows:
            age = L.days_ago(r["started_at"])
            fresh = age is not None and age <= 60
            if self.f_fresh.get() and not (fresh or r["new_assets_30d"]):
                continue
            shown += 1
            flags = []
            if fresh:
                flags.append("NEW")
            if r["new_assets_30d"]:
                flags.append(f"+{r['new_assets_30d']} assets")
            if r["gold_standard"]:
                flags.append("GSSH")
            if r["fast_payments"]:
                flags.append("FAST")
            tags = ["odd"] if shown % 2 else []
            if shown <= 10:
                tags.append("hot")
            elif fresh or r["new_assets_30d"]:
                tags.append("fresh")
            elif not r["web_assets"]:
                tags.append("dim")
            self.tree.insert("", "end", iid=r["handle"], tags=tags, values=(
                shown, f"{r['score']:.1f}", bar(r["score"]), r["name"][:26], r["web_assets"], r["wildcards"],
                r["max_sev"], r["pay_count"] or "", f"${r['pay_median']:,.0f}" if r["pay_median"] else "",
                " ".join(flags)))
            self.rows[r["handle"]] = r
        # stats
        allb = L.ranked(c, bounty_only=True, open_only=True)
        self.stat_vars["open"].set(str(len(allb)))
        self.stat_vars["fresh"].set(str(sum(1 for r in allb if (L.days_ago(r["started_at"]) or 999) <= 60)))
        self.stat_vars["assets"].set(str(sum(r["new_assets_30d"] for r in allb)))
        self.stat_vars["changes"].set(str(c.execute(
            "SELECT COUNT(*) FROM changes WHERE ts >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day')").fetchone()[0]))
        # feed
        self.feed.delete(0, "end")
        self.feed_handles = []
        icons = {"new_program": "◆", "new_scope": "+", "scope_bounty": "$", "bounties_on": "$", "state": "↺", "hunt": "◎",
                 "scope_removed": "−", "recon": "⌖"}
        for ch in c.execute("SELECT * FROM changes ORDER BY id DESC LIMIT 60"):
            self.feed.insert("end", f" {ch['ts'][5:16].replace('T', ' ')}  {icons.get(ch['kind'], '·')} "
                                    f"{ch['handle']:<26} {ch['detail']}")
            if ch["kind"] in ("new_program", "bounties_on"):
                self.feed.itemconfigure("end", fg=HOT)
            elif ch["kind"] in ("new_scope", "scope_bounty"):
                self.feed.itemconfigure("end", fg=GREEN)
            elif ch["kind"] == "hunt":
                self.feed.itemconfigure("end", fg=GOLD)
            elif ch["kind"] == "recon":
                self.feed.itemconfigure("end", fg="#7fb2d9" if "+0 new" in ch["detail"] or "baseline" in ch["detail"] else GREEN)
            self.feed_handles.append(ch["handle"])
        if not self.feed_handles:
            self.feed.insert("end", "  baseline captured — new programs and assets will stream in here on each sync")
        c.close()
        self.shown = shown
        self.update_status()
        if keep and sel in self.rows:
            self.tree.selection_set(sel)
            self.tree.see(sel)
        elif not keep and self.tree.get_children():
            first = sel if sel in self.rows else self.tree.get_children()[0]
            self.tree.selection_set(first)

    def select(self):
        s = self.tree.selection()
        if not s:
            return
        h = self.cur = s[0]
        r = self.rows[h]
        self.d_title.config(text=r["name"])
        self.d_sub.config(text=f"{h} · {r['submission_state']} · started {(r['started_at'] or '')[:10]} · "
                               f"score {r['score']:.1f}")
        self.d_why.config(state="normal")
        self.d_why.delete("1.0", "end")
        for w in json.loads(r["reasons"] or "[]"):
            bad = any(k in w for k in ("paused", "only", "closed"))
            self.d_why.insert("end", ("  ! " if bad else "  + "), "warn" if bad else "plus")
            self.d_why.insert("end", w + "\n")
        hdr = L.policy_header(r["policy"])
        if hdr:
            self.d_why.insert("end", "  ⚑ ", "warn")
            self.d_why.insert("end", f"required header  {hdr[0]}: {hdr[1]}\n")
        c = L.db()
        hu = c.execute("SELECT * FROM hunts WHERE handle=? ORDER BY run_at DESC LIMIT 1", (h,)).fetchone()
        self.cur_leads = hu["leads"] or (os.path.join(hu["dir"], "") if hu["dir"] else None) if hu else None
        if hu:
            self.d_why.insert("end", "  ◎ ", "plus")
            self.d_why.insert("end", f"hunted {hu['run_at'][:10]} ({hu['mode']}): {hu['hosts']} hosts · "
                                     f"{hu['live']} live · {hu['findings']} nuclei hits"
                                     f"{' · leads ready' if hu['leads'] else ''}\n")
        rc = c.execute("SELECT * FROM recon WHERE handle=? ORDER BY run_at DESC LIMIT 1", (h,)).fetchone()
        self.cur_recon = os.path.join(rc["dir"], "SUMMARY.md") if rc else None
        if rc:
            st = json.loads(rc["stats"] or "{}")
            new = "baseline" if st.get("baseline") else (f"+{st.get('new_hosts', 0)} hosts · +{st.get('new_live', 0)} live"
                                                          f" · {st.get('changed', 0)} changed")
            self.d_why.insert("end", "  ⌖ ", "plus")
            self.d_why.insert("end", f"recon {rc['run_at'][:10]} ({st.get('mode', 'passive')}): {st.get('hosts', 0)} hosts · "
                                     f"{st.get('live', 0)} live · {st.get('interesting', 0)} interesting URLs"
                                     f"{' · ' + str(st['dangling']) + ' dangling CNAME' if st.get('dangling') else ''} · {new}\n")
        self.d_why.config(state="disabled")
        self.scopes.delete(*self.scopes.get_children())
        for s in c.execute("SELECT * FROM scopes WHERE handle=? AND submission=1 ORDER BY bounty DESC, "
                           "CASE max_severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END",
                           (h,)):
            tags = ("new",) if (L.days_ago(s["created_at"]) or 999) <= 30 else (() if s["bounty"] else ("nob",))
            self.scopes.insert("", "end", iid=s["id"], tags=tags, values=(
                "★" if s["bounty"] else "", s["asset_type"], s["max_severity"] or "", s["identifier"]))
        c.close()

    # ------------------------------------------------------------------ actions
    def open_leads(self):
        p = getattr(self, "cur_leads", None)
        if not p:
            self.status.set(f"no hunt yet for {self.cur} — run `h1 hunt --only {self.cur}`")
            return
        subprocess.Popen(["xdg-open", p], start_new_session=True)

    def open_recon(self):
        p = getattr(self, "cur_recon", None)
        if not p or not os.path.exists(p):
            self.status.set(f"no recon yet for {self.cur} — run `h1 recon --only {self.cur}`")
            return
        subprocess.Popen(["xdg-open", p], start_new_session=True)

    def open_h1(self):
        if self.cur:
            subprocess.Popen(["xdg-open", f"https://hackerone.com/{self.cur}"], start_new_session=True)

    def export(self):
        if not self.cur:
            return
        c = L.db()
        d, files = L.export_targets(c, self.cur)
        c.close()
        self.status.set(f"exported {len(files)} lists → {d}")
        subprocess.Popen(["xdg-open", d], start_new_session=True)

    def copy_hosts(self):
        if not self.cur:
            return
        c = L.db()
        t = L.targets(c, self.cur)
        c.close()
        txt = "\n".join(t["wildcards"] + t["urls"] + t["cidrs"])
        self.clipboard_clear()
        self.clipboard_append(txt)
        self.status.set(f"copied {len(txt.splitlines())} targets to clipboard")

    def new_report(self):
        if not self.cur:
            return
        title = simpledialog.askstring("New report", f"Finding title for {self.cur}:", parent=self)
        if not title:
            return
        cmd = [sys.executable, "-I", H1, "report", "new", self.cur, "--title", title]
        sel = self.scopes.selection()
        if sel:
            cmd += ["--asset", self.scopes.item(sel[0], "values")[3]]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode:
            messagebox.showerror("report", out.stderr or out.stdout)
            return
        path = out.stdout.splitlines()[0]
        self.status.set(f"draft → {path}")
        subprocess.Popen(["xdg-open", path], start_new_session=True)

    def feed_jump(self, _):
        i = self.feed.curselection()
        if i and i[0] < len(self.feed_handles):
            h = self.feed_handles[i[0]]
            if h not in self.rows:
                self.f_bounty.set(False)
                self.f_open.set(False)
                self.f_fresh.set(False)
                self.q.set("")
                self.reload(keep=True)
            if h in self.rows:
                self.tree.selection_set(h)
                self.tree.see(h)

    def sync(self):
        if self.syncing:
            return
        self._svc_cache = (0.0, False)  # force a fresh systemd check
        if L.read_progress() is not None or self._service_running():
            self.status.set("a sync is already running — watch the bar below")
            return
        self.syncing = True
        self.sync_btn.state(["disabled"])
        self.status.set("syncing with HackerOne… (programs → scopes → payouts)")

        def run():
            p = subprocess.run([sys.executable, "-I", H1, "sync", "--quiet", "--notify"],
                               capture_output=True, text=True)
            self.after(0, lambda: self.sync_done(p))
        threading.Thread(target=run, daemon=True).start()

    def sync_done(self, p):
        self.syncing = False
        self.sync_btn.state(["!disabled"])
        if p.returncode:
            self.status.set("sync failed: " + (p.stderr or p.stdout).strip().splitlines()[-1][:120])
        else:
            self.reload(keep=True)

    def update_status(self):
        """Status line: last sync (local time), background sync in progress, next scheduled auto-sync."""
        if self.syncing:
            return
        last = self.last_seen_sync
        try:
            lt = L.parse_ts(last).astimezone().strftime("%H:%M") if last else "never"
        except Exception:  # noqa: BLE001
            lt = last or "never"
        parts = [f"{getattr(self, 'shown', 0)} programs", f"last sync {lt}"]
        sh = lambda *a: subprocess.run(["systemctl", "--user", *a], capture_output=True, text=True).stdout.strip()
        if sh("is-active", "h1-sync.service") in ("activating", "active"):
            parts.append("⟳ auto-sync running…")
        else:
            nxt = sh("show", "h1-sync.timer", "-p", "NextElapseUSecRealtime", "--value")
            m = re.search(r"(\d{2}:\d{2}):\d{2}", nxt)
            parts.append(f"next auto-sync {m.group(1)}" if m else "auto-sync timer OFF — widget will self-sync")
        self.status.set(" · ".join(parts))

    # ------------------------------------------------------------ sync progress
    def _service_running(self):
        """Is the systemd sync running? Cached for 5s so the 1s poll stays cheap."""
        t, val = self._svc_cache
        if time.time() - t > 5:
            out = subprocess.run(["systemctl", "--user", "is-active", "h1-sync.service"],
                                 capture_output=True, text=True).stdout.strip()
            val = out in ("activating", "active")
            self._svc_cache = (time.time(), val)
        return val

    def _set_prog_mode(self, mode):
        if mode == self.prog_mode:
            return
        self.prog_bar.stop()
        self.prog_bar.configure(mode=mode)
        if mode == "indeterminate":
            self.prog_bar.start(15)
        self.prog_mode = mode

    def poll_progress(self):
        try:
            st = L.read_progress()
            running = st is not None or self.syncing or self._service_running()
            if running:
                if not self.prog_shown:
                    self.prog.pack(fill="x", padx=14, pady=(2, 0), before=self.pw)
                    self.prog_shown = True
                if st:
                    self._set_prog_mode("determinate")
                    self.prog_bar["value"] = st.get("pct", 0)
                    ph, done, total, note = st["phase"], st.get("done", 0), st.get("total", 0), st.get("note", "")
                    step = {"programs": "1/4 programs", "scopes": "2/4 scopes", "payouts": "3/4 payouts",
                            "scoring": "4/4 scoring"}.get(ph, ph)
                    count = f" {done}/{total}" if total and ph in ("scopes", "payouts") else ""
                    self.prog_txt.set(f"{step}{count}" + (f" · {note}" if note else ""))
                    self.prog_pct.set(f"{st.get('pct', 0):.0f}%")
                else:  # running, but the writer predates progress reporting (or hasn't started yet)
                    self._set_prog_mode("indeterminate")
                    self.prog_txt.set("running… (waiting for progress data)")
                    self.prog_pct.set("")
            elif self.prog_shown:
                self.prog_bar.stop()
                self.prog_mode = None
                self.prog.pack_forget()
                self.prog_shown = False
                self._svc_cache = (0.0, False)
                self.reload(keep=True)  # pick up the freshly landed sync right away
                self.update_status()
        except Exception:  # noqa: BLE001 - progress is cosmetic
            pass
        self.after(1000, self.poll_progress)

    def tick(self):
        """Live reload when the timer (or another shell) lands a sync; self-sync if no timer is running."""
        try:
            c = L.db()
            last = L.meta_get(c, "last_sync")
            c.close()
            if last != self.last_seen_sync:
                self.reload(keep=True)
            self.update_status()
            age = L.days_ago(last) if last else None
            timer = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "h1-sync.timer"]).returncode == 0
            busy = self.syncing or L.read_progress() is not None or self._service_running()
            if not timer and not busy and (age is None or age * 1440 > AUTOSYNC_MIN):
                self.sync()
        except Exception as e:  # noqa: BLE001
            self.status.set(f"refresh error: {e}")
        self.after(RELOAD_MS, self.tick)


if __name__ == "__main__":
    Radar().mainloop()
