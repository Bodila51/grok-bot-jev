"""Paths, config, database (idempotent migrations), logging. Stdlib + pyyaml (from the Jev venv)."""
import hashlib, json, os, re, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # jevopus.py re-execs under the Jev venv; tolerate plain python for doctor
    yaml = None

def env(name, default=None):
    """JEVOPUS_<name>, falling back to the legacy FARM_<name> (pre-rename installs)."""
    return os.environ.get("JEVOPUS_" + name) or os.environ.get("FARM_" + name) or default


HOME = Path(env("HOME") or Path(__file__).resolve().parent.parent).resolve()
DB = HOME / "jevopus.db"
LEGACY_DB = HOME / "farm.db"  # pre-rename name; renamed to jevopus.db on first open
JOBS = HOME / "jobs"
LOG = HOME / "logs" / "routing.jsonl"
RECIPES = HOME / "recipes"
CONFIG = HOME / "config.json"
RENDER_PY = HOME / "tools" / "render-venv" / "bin" / "python"
SECRET_ENV_RE = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)
LIMIT_RE = re.compile(r"rate.?limit|quota|usage limit|too many requests|\b429\b|insufficient_quota|limit reached", re.I)


def jev_dir():
    """The Jev router repo (grok-bot-jev preferred, muse-jev-playbook compatible). Needs .venv."""
    pinned = HOME / ".jev_dir"  # written by the installer
    cands = [env("JEV_DIR"), pinned.read_text().strip() if pinned.exists() else None, HOME.parent / "grok-bot-jev", "/workspace/grok-bot-jev",
             HOME.parent / "muse-jev-playbook", "/workspace/muse-jev-playbook"]
    for c in cands:
        if c and (Path(c) / ".venv" / "bin" / "python").exists():
            return Path(c)
    return None


DEFAULT_CONFIG = {
    "_note": "Jevopus settings. Model descriptions are editable defaults (task fit), not benchmarks.",
    "models": {
        "gpt-6-astra": {"cli": "codex", "tier": "volume",
                        "fit": "Fast general default: everyday coding, small scripts, edits, short well-specified tasks."},
        "gpt-6-sol": {"cli": "codex", "tier": "strong",
                      "fit": "Best for creative, visual and long craft work: design, motion/video, polished writing, larger builds."},
        "gpt-6-luna": {"cli": "codex", "tier": "volume",
                       "fit": "Cheaper volume work: research summaries, data cleanup, bulk drafts, routine checks."},
        "opus": {"cli": "claude", "tier": "strong", "id": "claude-opus (CLI alias opus)",
                 "fit": "Claude Code: hard multi-file refactors and careful long reasoning."},
        "sonnet": {"cli": "claude", "tier": "strong", "id": "claude-sonnet-5 (CLI alias sonnet)",
                   "fit": "Claude Code, balanced: everyday and mid-size coding, multi-step edits, solid writing; faster and lighter than Opus."},
        "haiku": {"cli": "claude", "tier": "volume", "id": "claude-haiku-4-5 (CLI alias haiku)",
                  "fit": "Claude Code, fast and light: small edits, quick scripts, summaries, bulk routine checks; weaker on hard reasoning."},
    },
    "default_model": "gpt-6-astra",
    "verify": {"max_fix_rounds": 1, "pass_min": 0.5},
    "reuse": {"candidates": 20},
    "guard": {"confirm_min": 0.5},
    "cooldown_sec": 3600,
    "lease_sec": 7200,
    "run_timeout_sec": 1800,
}


def merge_models(user_models):
    """Add default models (and missing fields of known models) without overwriting any user-edited value."""
    out = {m: dict(v) for m, v in (user_models or {}).items()}
    for m, dv in DEFAULT_CONFIG["models"].items():
        if m not in out: out[m] = dict(dv)
        elif isinstance(out[m], dict):
            for f, fv in dv.items(): out[m].setdefault(f, fv)
    return out


def config():
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        user = json.loads(CONFIG.read_text())
        if isinstance(user.get("models"), dict):
            merged = merge_models(user["models"])
            if merged != user["models"]:  # upgrade: persist new default models/fields, keep user edits
                user["models"] = merged
                try: CONFIG.write_text(json.dumps(user, indent=2, ensure_ascii=False) + "\n")
                except OSError: pass
        for k, v in user.items():
            cfg[k] = {**cfg[k], **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) and k != "models" else v
    except Exception as e:
        print(f"warning: config.json unreadable ({e}); using defaults")
    return cfg


def cli_model(cfg, model):
    """Name passed to the worker CLI (--model / -m): config `cli_model` override, else the model key (claude aliases)."""
    return (cfg["models"].get(model) or {}).get("cli_model") or model


def playbook():
    """Jev router config.yaml (enabled/mode/policy/thresholds/limits) with safe defaults."""
    p = {"enabled": True, "mode": "shadow", "model": "jev-latest",
         "policy": {"act_min": 0.8, "surface_min": 0.5},
         "thresholds": {"min_choice_confidence": 0.55, "reuse_min": 0.65, "stop_retry_min": 0.55},
         "limits": {"max_retries_same_error": 1}}
    d = jev_dir()
    if d and yaml and (d / "config.yaml").exists():
        try:
            u = yaml.safe_load((d / "config.yaml").read_text()) or {}
            for k, v in u.items():
                p[k] = {**p[k], **v} if isinstance(v, dict) and isinstance(p.get(k), dict) else v
        except Exception:
            pass
    return p


def band(conf, pol):
    return "act" if conf >= pol["act_min"] else "surface" if conf >= pol["surface_min"] else "escalate"


now = time.time
def iso(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def goal_hash(goal): return hashlib.sha1(" ".join(goal.lower().split()).encode()).hexdigest()[:12]
def err_sig(text): return hashlib.sha1(re.sub(r"[\d\W]+", " ", (text or "").lower()).strip()[:200].encode()).hexdigest()[:10]


def log(rec):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({"ts": iso(), **rec}, ensure_ascii=False) + "\n")


def worker_env(seat):
    """Env for a worker CLI: no credentials of any kind (API keys, tokens), seat-specific config dir."""
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV_RE.search(k)}
    env["CODEX_HOME" if seat["cli"] == "codex" else "CLAUDE_CONFIG_DIR"] = seat["home_dir"]
    env["JEVOPUS_RENDER_PY"] = str(RENDER_PY)
    return env


# ---------- schema ----------
STATUSES = ("queued", "leased", "running", "done", "failed", "needs_review", "blocked", "cached")
JOBS_DDL = f"""CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, goal TEXT, constraints TEXT, done_when TEXT, kind TEXT,
  seat_id TEXT, status TEXT CHECK(status IN {STATUSES}),
  lease_until REAL, created_at TEXT, result_path TEXT, route TEXT, tier TEXT, route_note TEXT)"""
JOB_COLS = {  # added columns (idempotent ALTERs)
    "model": "TEXT", "effort": "TEXT", "pin_seat": "TEXT", "model_source": "TEXT", "prompt": "TEXT",
    "tokens": "INTEGER DEFAULT 0", "secs": "INTEGER", "started_at": "TEXT", "finished_at": "TEXT",
    "session_id": "TEXT", "verify_p": "REAL", "verify_status": "TEXT", "fix_rounds": "INTEGER DEFAULT 0",
    "goal_hash": "TEXT", "err_sig": "TEXT", "requires_confirm": "INTEGER DEFAULT 0", "confirmed": "INTEGER DEFAULT 0",
    "from_agent": "TEXT", "recipe": "TEXT", "recipe_vars": "TEXT", "cached_from": "TEXT", "forced": "INTEGER DEFAULT 0",
    "cli_config": "TEXT", "attach": "TEXT", "feedback": "TEXT", "feedback_note": "TEXT", "model_evidence": "TEXT"}
SCHEMA = JOBS_DDL + """;
CREATE TABLE IF NOT EXISTS seats(
  id TEXT PRIMARY KEY, cli TEXT, home_dir TEXT, model TEXT,
  tier TEXT CHECK(tier IN ('strong','volume')), enabled INTEGER,
  status TEXT CHECK(status IN ('idle','busy','cooldown')) DEFAULT 'idle',
  cooldown_until REAL, auth TEXT CHECK(auth IN ('subscription','api_key')), note TEXT);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, job_id TEXT, kind TEXT, answer TEXT,
  conf REAL, band TEXT, applied INTEGER, jev_used INTEGER, outcome TEXT);
CREATE TABLE IF NOT EXISTS seat_events(ts TEXT, epoch REAL, seat_id TEXT, event TEXT, job_id TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""
CODEX_MODELS = "gpt-6-astra,gpt-6-sol,gpt-6-luna"
CLAUDE_MODELS = "opus,sonnet,haiku"  # claude CLI aliases


def seeds():  # id, cli, home_dir, model, tier, enabled, auth, note, models  -- all DISABLED until setup-seat passes
    s = HOME / "seats"
    return [("codex-sub", "codex", str(s / "codex-sub/codex"), "gpt-6-astra", "volume", 0, "subscription",
             "ChatGPT subscription; run: jevopus.py setup-seat codex-sub", CODEX_MODELS),
            ("codex-1", "codex", str(s / "codex-1/codex"), "gpt-6-astra", "volume", 0, "api_key",
             "OpenAI API key; run: jevopus.py setup-seat codex-api", CODEX_MODELS),
            ("claude-strong", "claude", str(s / "claude-strong/claude"), "opus", "strong", 0, "subscription",
             "Claude Code; run: jevopus.py setup-seat claude-strong", CLAUDE_MODELS)]


def db():
    if not DB.exists() and LEGACY_DB.exists():
        LEGACY_DB.rename(DB)
    c = sqlite3.connect(DB, timeout=30); c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    have = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
    for col, typ in JOB_COLS.items():
        if col not in have: c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
    if "models" not in {r[1] for r in c.execute("PRAGMA table_info(seats)")}:
        c.execute("ALTER TABLE seats ADD COLUMN models TEXT")
    c.execute("UPDATE seats SET models=? WHERE models IS NULL AND cli='codex'", (CODEX_MODELS,))
    c.execute("UPDATE seats SET models=model WHERE models IS NULL")
    for s in c.execute("SELECT id, models FROM seats WHERE cli='claude'").fetchall():  # upgrade: offer new claude models
        have_m = [m for m in (s["models"] or "").split(",") if m]
        new = have_m + [m for m in CLAUDE_MODELS.split(",") if m not in have_m]
        if new != have_m: c.execute("UPDATE seats SET models=? WHERE id=?", (",".join(new), s["id"]))
    if "evidence" not in {r[1] for r in c.execute("PRAGMA table_info(decisions)")}:
        c.execute("ALTER TABLE decisions ADD COLUMN evidence TEXT")
    sql = c.execute("SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0]
    if "needs_review" not in sql:  # widen the status CHECK: rebuild table once (v1 -> v2)
        cols = ",".join(r[1] for r in c.execute("PRAGMA table_info(jobs)"))
        c.executescript(f"BEGIN; ALTER TABLE jobs RENAME TO jobs_v1; {JOBS_DDL}; COMMIT;")
        for col, typ in JOB_COLS.items(): c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
        c.execute(f"INSERT INTO jobs({cols}) SELECT {cols} FROM jobs_v1"); c.execute("DROP TABLE jobs_v1")
    for r in c.execute("SELECT id, goal FROM jobs WHERE goal_hash IS NULL").fetchall():
        c.execute("UPDATE jobs SET goal_hash=? WHERE id=?", (goal_hash(r["goal"] or ""), r["id"]))
    if not c.execute("SELECT 1 FROM seats").fetchone():
        c.executemany("INSERT INTO seats(id,cli,home_dir,model,tier,enabled,auth,note,models) VALUES(?,?,?,?,?,?,?,?,?)", seeds())
    c.execute("INSERT OR REPLACE INTO meta VALUES('schema','2')")
    c.commit()
    return c


def seat_event(c, seat_id, event, job_id="", note=""):
    c.execute("INSERT INTO seat_events VALUES(?,?,?,?,?,?)", (iso(), now(), seat_id, event, job_id, note))


def record_decisions(c, job_id, items, jev_used):
    """items: list of (kind, answer, conf, band, applied[, evidence])."""
    for k, a, conf, b, ap, *ev in items:
        c.execute("INSERT INTO decisions(ts,job_id,kind,answer,conf,band,applied,jev_used,evidence) VALUES(?,?,?,?,?,?,?,?,?)",
                  (iso(), job_id, k, str(a), conf, b, int(bool(ap)), int(bool(jev_used)), ev[0] if ev else None))


def refresh_seats(c):
    c.execute("UPDATE seats SET status='idle', cooldown_until=NULL WHERE status='cooldown' AND cooldown_until<?", (now(),))
    for j in c.execute("SELECT * FROM jobs WHERE status='leased' AND lease_until<?", (now(),)).fetchall():
        c.execute("UPDATE jobs SET status='queued', seat_id=NULL, lease_until=NULL WHERE id=?", (j["id"],))
        c.execute("UPDATE seats SET status='idle' WHERE id=?", (j["seat_id"],))
    c.commit()


def enabled_models(c, cfg):
    """model -> [seat ids] for enabled seats (a model must appear in config.models to be Jev-selectable)."""
    out = {}
    for s in c.execute("SELECT * FROM seats WHERE enabled=1"):
        for m in (s["models"] or s["model"] or "").split(","):
            m = m.strip()
            if m: out.setdefault(m, []).append(s["id"])
    return out
