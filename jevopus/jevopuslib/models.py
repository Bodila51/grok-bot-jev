"""Model names, availability and the 'model not connected' note (status needs_model).

resolve()      : exact id, common alias ('Claude Sonnet', 'GPT-6 Sol', 'Astra') or unambiguous fuzzy match ('sonet').
availability() : why a model can or cannot run right now:
                 unknown | not_connected (seat disabled / CLI missing) | seat_mismatch | cooldown | over_limit.
issue()        : the machine-readable + human-readable note stored on a needs_model job."""
import difflib, json, re, shutil, time
from .core import HOME, config, db, enabled_models, now

SETUP_ALIAS = {"codex-1": "codex-api"}


def _norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def alias_pairs(cfg):
    """(raw alias text, model id) pairs, e.g. ('Claude Sonnet', 'sonnet')."""
    out = []
    def add(a, m):
        if a: out.append((a, m))
    for m, d in cfg["models"].items():
        d = d if isinstance(d, dict) else {}
        add(m, m); add(d.get("cli_model"), m)
        if d.get("id"): add(d["id"].split()[0], m)
        short = re.sub(r"^(gpt-?\d+(\.\d+)?-?|claude-?)", "", m)
        if short != m:
            add(short, m)
        if d.get("cli") == "claude":
            add(f"claude {m}", m); add(f"claude-{m}", m)
            if d.get("id"): add(re.sub(r"-\d+(-\d+)*$", "", d["id"].split()[0]), m)  # claude-sonnet-5 -> claude-sonnet
        if m.startswith("gpt-"):
            add(m.replace("gpt-", "GPT-").replace("-" + short, " " + short.capitalize()), m); add("ChatGPT " + short.capitalize(), m)
        if short != m: out.append((short.capitalize(), m))
        if d.get("cli") == "claude": out.append((f"Claude {m.capitalize()}", m))
    return out


def aliases(cfg):
    """normalized alias -> model id (unambiguous aliases only)."""
    cand = {}
    for a, m in alias_pairs(cfg):
        k = _norm(a)
        if k: cand.setdefault(k, set()).add(m)
    return {k: next(iter(v)) for k, v in cand.items() if len(v) == 1}


def resolve(cfg, name):
    """-> {"model": id|None, "how": exact|alias|fuzzy|None, "suggestions": [ids]}"""
    if not name: return {"model": None, "how": None, "suggestions": []}
    if name in cfg["models"]: return {"model": name, "how": "exact", "suggestions": []}
    al = aliases(cfg); k = _norm(name)
    if k in al: return {"model": al[k], "how": "alias", "suggestions": []}
    inside = list(dict.fromkeys(al[x] for x in al if len(k) >= 3 and k in x))
    if len(inside) > 1: return {"model": None, "how": None, "suggestions": inside}  # 'claude', 'gpt6' are ambiguous
    close = difflib.get_close_matches(k, list(al), n=6, cutoff=0.72)
    hits = list(dict.fromkeys(al[c] for c in close))
    if len(hits) == 1: return {"model": hits[0], "how": "fuzzy", "suggestions": hits}
    if not hits:  # word-level: 'claude sonet 5' -> any alias containing a close word
        words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) >= 3 and w not in ("claude", "gpt", "model", "openai", "chatgpt")]
        for w in words:
            hits += [al[c] for c in difflib.get_close_matches(w, list(al), n=4, cutoff=0.75)]
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1: return {"model": hits[0], "how": "fuzzy", "suggestions": hits}
    return {"model": None, "how": None, "suggestions": hits[:3]}


def providers(c, cfg, model):
    """All seats (enabled or not) that offer the model; falls back to seats with the model's CLI."""
    seats = c.execute("SELECT * FROM seats").fetchall()
    out = [s for s in seats if model in (s["models"] or s["model"] or "").split(",")]
    cli = (cfg["models"].get(model) or {}).get("cli")
    return out or [s for s in seats if cli and s["cli"] == cli]


def setup_cmd(seat_id): return f"python3 {HOME}/jevopus.py setup-seat {SETUP_ALIAS.get(seat_id, seat_id)}"


def availability(c, cfg, model, pin_seat=None, large=False, t=None):
    """-> dict(available, case, reason, seats, connect, resets_at)."""
    from . import limits as lim
    t = t or now()
    if model not in cfg["models"] and not providers(c, cfg, model):
        return {"available": False, "case": "unknown", "reason": f"'{model}' is not a model Jevopus knows",
                "seats": [], "connect": "use one of the names from `jevopus.py models`", "resets_at": None}
    prov = providers(c, cfg, model)
    if pin_seat: prov = [s for s in prov if s["id"] == pin_seat]
    if not prov:
        return {"available": False, "case": "seat_mismatch", "reason": f"seat {pin_seat} does not offer {model}",
                "seats": [], "connect": f"drop --seat {pin_seat} or pick a model that seat offers (jevopus.py models)", "resets_at": None}
    en = [s for s in prov if s["enabled"]]
    if not en:
        why = []
        for s in prov:
            if not shutil.which(s["cli"]): why.append(f"{s['id']}: {s['cli']} CLI not installed")
            else: why.append(f"{s['id']}: not connected ({s['note'] or 'disabled'})")
        steps = []
        for s in prov:
            if s["cli"] == "claude":
                steps.append(("npm install -g @anthropic-ai/claude-code; " if not shutil.which("claude") else "")
                             + f"CLAUDE_CONFIG_DIR={s['home_dir']} claude  (then /login), then {setup_cmd(s['id'])}")
            else: steps.append(setup_cmd(s["id"]))
        return {"available": False, "case": "not_connected", "reason": f"{model} runs on seat {', '.join(s['id'] for s in prov)}, "
                "which is not connected: " + "; ".join(why), "seats": [s["id"] for s in prov],
                "connect": " | ".join(steps), "resets_at": None}
    free, resets, why = [], [], []
    for s in en:
        if s["status"] == "cooldown" and s["cooldown_until"] and s["cooldown_until"] > t:
            resets.append(s["cooldown_until"]); why.append(f"{s['id']} cooling down after a usage limit until {lim.fmt_t(s['cooldown_until'])}")
            continue
        m = lim.max_used(c, s["id"], t) if large else None
        if m and m[0] >= float(cfg["limits"]["skip_above_percent"]):
            if m[1]["resets_at"]: resets.append(m[1]["resets_at"])
            why.append(f"{s['id']} at {m[0]:.0f}% of its {m[1]['window']} limit (provider-reported), resets {lim.fmt_t(m[1]['resets_at'])}")
            continue
        free.append(s)
    if free:
        return {"available": True, "case": None, "reason": "", "seats": [s["id"] for s in free], "connect": "", "resets_at": None}
    case = "cooldown" if all("cooling" in w for w in why) else "over_limit"
    r = min(resets) if resets else None
    return {"available": False, "case": case, "reason": "; ".join(why), "seats": [s["id"] for s in en],
            "connect": f"wait until {lim.fmt_t(r)} (then run jevopus.py tick; the job is re-checked automatically)" if r else "wait",
            "resets_at": r}


def alternatives(c, cfg, job, exclude, jev_pick=None, n=2, large=False):
    """1-2 available models: the normal model choice first (Jev's pick if confident, else default_model), then the
    next by past verified passes, then config order."""
    from . import evidence as ev_mod
    avail = enabled_models(c, cfg)
    ok = [m for m in cfg["models"] if m in avail and m != exclude
          and availability(c, cfg, m, job["pin_seat"] if job["pin_seat"] and job["pin_seat"] in avail[m] else None, large=large)["available"]]
    if not ok: return []
    order = []
    if jev_pick in ok: order.append(jev_pick)
    if cfg["default_model"] in ok and cfg["default_model"] not in order: order.append(cfg["default_model"])
    try: counts = ev_mod.build(c, job, ok)["counts"]
    except Exception: counts = {}
    for m in sorted(ok, key=lambda m: -((counts.get(m) or {}).get("all") or {}).get("pass", 0)):
        if m not in order: order.append(m)
    return [{"model": m, "fit": cfg["models"][m].get("fit", ""), "seat": ",".join(avail[m])} for m in order[:n]]


def issue(c, cfg, job, requested, av, suggestions=(), jev_pick=None, large=False):
    alts = alternatives(c, cfg, job, requested, jev_pick, large=large)
    d = {"requested": requested, "case": av["case"], "reason": av["reason"], "seats": av["seats"], "connect": av["connect"],
         "resets_at": av["resets_at"], "suggestions": list(suggestions), "alternatives": alts,
         "proceed": ([f"jevopus.py repin {job['id']} {alts[0]['model']}"] if alts else [])
                    + [f"jevopus.py repin {job['id']} <model>", f"jevopus.py cancel {job['id']}"]}
    return d


def human(d, job_id=""):
    head = {"unknown": "MODEL NOT FOUND", "not_connected": "MODEL NOT CONNECTED", "seat_mismatch": "MODEL NOT ON THAT SEAT",
            "cooldown": "MODEL TEMPORARILY UNAVAILABLE (cooldown)", "over_limit": "MODEL TEMPORARILY UNAVAILABLE (usage limit)"}.get(d["case"], "MODEL UNAVAILABLE")
    L = [f"{head}: requested '{d['requested']}'" + (f" (job {job_id})" if job_id else ""), f"  why: {d['reason']}"]
    if d.get("suggestions"): L.append("  did you mean: " + ", ".join(d["suggestions"]))
    L.append(f"  how to connect: {d['connect']}")
    for i, a in enumerate(d.get("alternatives") or [], 1):
        L.append(f"  alternative {i}: {a['model']} (seat {a['seat']}) - {a['fit']}")
    if not d.get("alternatives"): L.append("  alternatives: none available right now (no other model on a usable seat)")
    L.append("  proceed: " + " | ".join(d["proceed"]))
    return "\n".join(L)


def cmd_models(a):
    from .core import refresh_seats
    from . import limits as lim
    c = db(); cfg = config(); refresh_seats(c)
    try: lim.refresh(c)
    except Exception: pass
    al = aliases(cfg); pairs = alias_pairs(cfg); names = list(cfg["models"])
    for s in c.execute("SELECT * FROM seats"):
        names += [m for m in (s["models"] or "").split(",") if m and m not in names]
    print("MODELS  (available = an enabled seat offers it and is not cooling down; pin with --model or JEVOPUS JOB model:)")
    for m in names:
        av = availability(c, cfg, m); d = cfg["models"].get(m) or {}
        seats = ", ".join(s["id"] + ("" if s["enabled"] else "(off)") for s in providers(c, cfg, m)) or "-"
        print(f"  {m:12} {'yes' if av['available'] else 'NO ':3}  seat={seats:20} {d.get('fit', '(not in config.json models)')}")
        if not av["available"]: print(f"  {'':12}      why: {av['reason']}\n  {'':12}      connect: {av['connect']}")
        seen = {}
        for x, v in sorted(pairs, key=lambda t: t[0] == t[0].lower()):  # prefer 'Claude Sonnet' over 'claude sonnet'
            if v == m and x != m and al.get(_norm(x)) == m and _norm(x) != _norm(m): seen.setdefault(_norm(x), x)
        akas = list(seen.values())
        if akas: print(f"  {'':12}      also accepted: {', '.join(akas[:5])}")
    print(f"  default model: {cfg['default_model']}")
