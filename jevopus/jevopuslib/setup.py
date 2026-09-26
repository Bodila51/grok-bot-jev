"""doctor + setup-seat helpers. Secrets: presence checks only; keys go to CLIs via stdin, never printed."""
import os, re, shutil, sqlite3, subprocess, sys, time
from pathlib import Path
from .core import DB, HOME, RENDER_PY, config, db, jev_dir, log, playbook, seat_event, worker_env

COLOR = sys.stdout.isatty() or os.environ.get("FORCE_COLOR")
def _c(code, s): return f"\033[{code}m{s}\033[0m" if COLOR else s
OK, BAD, OPT = _c("32", "[ OK ]"), _c("31", "[FAIL]"), _c("33", "[ -- ]")
KEY_HINT = "if a key you saved isn't visible, update the computer in Grok Bot settings and re-check"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
OPENAI_KEY_VARS = ("JEVOPUS_OPENAI_API_KEY", "FARM_OPENAI_API_KEY", "OPENAI_API_KEY")  # first set wins


def openai_key_var():
    """Name (never the value) of the first OpenAI key variable that is set."""
    return next((v for v in OPENAI_KEY_VARS if os.environ.get(v)), None)


def _run(argv, env=None, timeout=20, cwd=None, input=None):
    try:
        p = subprocess.run(argv, env=env, cwd=cwd, input=input, capture_output=True, text=True, timeout=timeout,
                           stdin=None if input is not None else subprocess.DEVNULL)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except FileNotFoundError: return 127, "", "not found"
    except subprocess.TimeoutExpired: return 124, "", "timeout"


def codex_logged_in(seat):
    if not Path(seat["home_dir"]).exists(): return False, "no CODEX_HOME yet"
    rc, out, err = _run(["codex", "login", "status"], env=worker_env(seat))
    txt = ANSI.sub("", out + err).strip()
    line = txt.splitlines()[-1][:80] if txt else f"rc={rc}"
    return rc == 0, re.sub(r"\s*-\s*\S*\*\S*", "", re.sub(r"(sk|key)[-_]\S+", "***", line))  # never show key fragments


def claude_logged_in(seat):
    d = Path(seat["home_dir"])
    return any((d / f).exists() for f in (".credentials.json", ".claude.json")), str(d)


def cmd_doctor(a):
    rows = []
    def chk(ok, name, detail="", fix="", optional=False):
        rows.append(ok); tag = OK if ok else (OPT if optional else BAD)
        print(f"{tag} {name}" + (f" - {detail}" if detail else "") + ("" if ok or not fix else f"\n        fix: {fix}"))
        return ok
    print(f"Jevopus doctor  (JEVOPUS_HOME={HOME})")
    rc, out, _ = _run(["node", "-v"]); m = re.match(r"v(\d+)", out.strip())
    chk(bool(m) and int(m[1]) >= 22, "node >= 22", out.strip() or "missing", "rerun jevopus_install.sh (installs Node 22 via NodeSource)")
    clis = 0
    for cli, label, pkg in (("codex", "codex CLI (Codex / ChatGPT)", "@openai/codex"),
                            ("claude", "claude CLI (Claude Code)", "@anthropic-ai/claude-code")):
        rc, out, _ = _run([cli, "--version"]); clis += rc == 0
        chk(rc == 0, label, out.strip() or "not installed", f"npm install -g {pkg}", optional=True)
    chk(clis > 0, "at least one worker CLI (Codex or Claude Code)", f"{clis} installed",
        "npm install -g @openai/codex  and/or  npm install -g @anthropic-ai/claude-code")
    try:
        c = db(); schema = c.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0]
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        chk(schema == "2" and {"jobs", "seats", "decisions", "seat_events"} <= tables, "jevopus.db schema", f"v{schema}, {DB}")
    except Exception as e:
        chk(False, "jevopus.db schema", str(e)[:100], f"python3 {HOME}/jevopus.py init"); c = None
    if c:
        for s in c.execute("SELECT * FROM seats"):
            opt = not s["enabled"]  # Codex and Claude Code seats are treated alike: red only if enabled but broken
            if not shutil.which(s["cli"]):
                chk(False, f"seat {s['id']} login", f"{s['cli']} not installed", f"python3 {HOME}/jevopus.py setup-seat {s['id']}", optional=opt); continue
            ok, det = codex_logged_in(s) if s["cli"] == "codex" else claude_logged_in(s)
            name = "codex-api" if s["id"] == "codex-1" else s["id"]
            chk(ok, f"seat {s['id']} login", f"{det}; enabled={s['enabled']}" + (f"; {s['note']}" if s["note"] else ""),
                f"python3 {HOME}/jevopus.py setup-seat {name}", optional=opt)
        n_en = c.execute("SELECT COUNT(*) FROM seats WHERE enabled=1").fetchone()[0]
        chk(n_en > 0, "at least one enabled seat", f"{n_en} enabled", f"python3 {HOME}/jevopus.py setup-seat codex-sub  (or claude-strong)")
        try:
            from . import limits
            limits.refresh(c); print(f"[info] limits - {limits.short_line(c, config())}  (details: jevopus.py limits)")
        except Exception as e: print(f"[info] limits - unavailable ({type(e).__name__})")
    jd = jev_dir()
    chk(jd is not None, "Jev router repo + venv", str(jd), "rerun jevopus_install.sh (clones grok-bot-jev and creates .venv)")
    if jd: chk((jd / "config.yaml").exists(), "Jev config.yaml", f"mode={playbook().get('mode')}", f"cp {jd}/config.example.yaml {jd}/config.yaml")
    if c:
        try:
            from .reports import mode_progress_line
            print(f"[info] {mode_progress_line(c)}")
        except Exception as e: print(f"[info] Jev mode progress unavailable ({type(e).__name__})")
    has_ts = bool(os.environ.get("TYPESAFE_API_KEY"))
    chk(has_ts, "TYPESAFE_API_KEY present", "set" if has_ts else "missing", f"add the TypeSafe key as a secret; {KEY_HINT}")
    if has_ts:
        try:
            from . import jev
            t0 = time.time(); p = jev.ping(playbook())
            chk(p > 0.5, "live Jev call", f"p={p}, {int((time.time()-t0)*1000)} ms")
        except Exception as e:
            chk(False, "live Jev call", f"{type(e).__name__}: {str(e)[:120]}", f"check the key/network; {KEY_HINT}")
    has_oa = bool(openai_key_var())
    chk(has_oa, "OpenAI API key (codex-api seat only)", "set" if has_oa else "missing",
        f"only needed for setup-seat codex-api; {KEY_HINT}", optional=True)
    if RENDER_PY.exists():
        rc, _, err = _run([str(RENDER_PY), "-c", "import PIL, numpy, cairo"])
        chk(rc == 0, "render venv (Pillow/numpy/pycairo)", str(RENDER_PY) if rc == 0 else err.strip()[-120:],
            "rerun jevopus_install.sh without --no-render", optional=True)
    else:
        chk(False, "render venv (optional, motion-video recipe)", "not installed", "rerun jevopus_install.sh without --no-render", optional=True)
    chk(bool(shutil.which("ffmpeg")), "ffmpeg (optional, video recipes)", shutil.which("ffmpeg") or "missing",
        "sudo apt-get install -y ffmpeg", optional=True)


# ---------- setup-seat ----------
SEAT_ALIASES = {"codex-sub": "codex-sub", "codex-api": "codex-1", "codex-1": "codex-1", "claude-strong": "claude-strong"}


def live_test(seat, model):
    tdir = Path(seat["home_dir"]).parent / "selftest"; tdir.mkdir(parents=True, exist_ok=True)
    msg = "Reply with exactly the single word JEVOPUS_OK and nothing else. Do not run any commands."
    if seat["cli"] == "codex":
        argv = ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "-m", model, msg]
    else:
        argv = ["claude", "-p", msg, "--model", model]
    rc, out, err = _run(argv, env=worker_env(seat), timeout=240, cwd=tdir)
    limited = bool(re.search(r"rate.?limit|quota|usage limit|429|insufficient_quota", out + err, re.I))
    return rc == 0 and "JEVOPUS_OK" in out, limited, ANSI.sub("", err).strip()[-300:]


def _enable(c, seat, ok, limited, err):
    if ok:
        c.execute("UPDATE seats SET enabled=1, status='idle', note=? WHERE id=?", (f"live test passed {time.strftime('%Y-%m-%d')}", seat["id"]))
        seat_event(c, seat["id"], "enabled"); print(f"{OK} live test passed -> seat {seat['id']} ENABLED")
    else:
        why = "usage/quota limit" if limited else "test failed"
        c.execute("UPDATE seats SET enabled=0, note=? WHERE id=?", (f"disabled: {why}", seat["id"]))
        print(f"{BAD} live test failed ({why}); seat {seat['id']} stays disabled.\n        detail: {err[-200:]}")
    c.commit(); log({"event": "setup_seat", "seat": seat["id"], "ok": ok, "limited": limited})


def cmd_setup_seat(a):
    sid = SEAT_ALIASES.get(a.seat)
    if not sid: sys.exit("seat must be codex-sub | codex-api | claude-strong")
    c = db(); seat = c.execute("SELECT * FROM seats WHERE id=?", (sid,)).fetchone()
    if a.disable:
        c.execute("UPDATE seats SET enabled=0, note='disabled by user' WHERE id=?", (sid,)); c.commit(); print(f"{sid} disabled"); return
    home = Path(seat["home_dir"]); home.mkdir(parents=True, exist_ok=True)
    model = seat["model"]
    if seat["cli"] == "codex" and not shutil.which("codex"): sys.exit("codex not installed: npm install -g @openai/codex")
    if sid == "codex-sub":
        ok, det = codex_logged_in(seat)
        if not ok or "API key" in det:
            logf, pidf = home.parent / "device_auth.log", home.parent / "device_auth.pid"
            alive = pidf.exists() and Path(f"/proc/{pidf.read_text().strip()}").exists()
            if not alive:
                p = subprocess.Popen(["codex", "login", "--device-auth"], env=worker_env(seat), stdout=open(logf, "w"),
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
                pidf.write_text(str(p.pid))
            url = code = None
            for _ in range(40):
                txt = ANSI.sub("", logf.read_text()) if logf.exists() else ""
                url = url or (re.search(r"https://\S+", txt) or [None])[0]
                code = code or (re.search(r"\b[A-Z0-9]{4,5}-[A-Z0-9]{4,6}\b", txt) or [None])[0]
                if url and code: break
                time.sleep(0.5)
            print("ChatGPT subscription login (device auth) is running in the background.")
            print(f"  1. Open: {url or '(see ' + str(logf) + ')'}\n  2. Enter code: {code or '(see log)'}")
            print("  3. Approve in your ChatGPT account (the code expires in ~15 minutes).")
            print(f"  4. Then run again: python3 {HOME}/jevopus.py setup-seat codex-sub   (tests and enables the seat)")
            print("  Note: this seat runs with API-key env vars removed, so it always uses the subscription.")
            return
        print(f"login: {det}; running live test with {model}...")
        _enable(c, seat, *live_test(seat, model))
    elif sid == "codex-1":
        var = openai_key_var()
        if not var:
            print(f"{BAD} no JEVOPUS_OPENAI_API_KEY / FARM_OPENAI_API_KEY / OPENAI_API_KEY in the environment.\n        Save one as a secret; {KEY_HINT}"); return
        env = worker_env(seat)
        rc, out, err = _run(["codex", "login", "--with-api-key"], env=env, input=os.environ[var] + "\n", timeout=60)
        print(f"api-key login from ${var} via stdin: {'ok' if rc == 0 else 'failed rc=%d' % rc}")
        if rc != 0: return
        _enable(c, seat, *live_test(seat, model))
    else:
        if not shutil.which("claude"):
            print("Claude Code CLI not found. Install it:\n  npm install -g @anthropic-ai/claude-code")
        ok, det = claude_logged_in(seat)
        if not ok:
            print(f"Log in once, interactively, with its own config dir:\n  CLAUDE_CONFIG_DIR={home} claude   # then type /login")
            print(f"Then run: python3 {HOME}/jevopus.py setup-seat claude-strong   (tests and enables the seat)"); return
        _enable(c, seat, *live_test(seat, model))
