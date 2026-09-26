#!/usr/bin/env bash
# Jevopus installer v2.0.20260926 (from a repo clone). Idempotent; safe to re-run. Never prints secrets.
# usage: [JEVOPUS_HOME=/workspace/jevopus] [JEVOPUS_JEV_DIR=...] bash jevopus/install.sh [--no-render] [--skip-node] [--no-doctor]
set -uo pipefail
JEVOPUS_HOME="${JEVOPUS_HOME:-${FARM_HOME:-/workspace/jevopus}}"   # FARM_* = legacy names, still honored
JEVOPUS_JEV_DIR="${JEVOPUS_JEV_DIR:-${FARM_JEV_DIR:-}}"
JEV_URL="${JEVOPUS_JEV_REPO:-${FARM_JEV_REPO:-https://github.com/Bodila51/grok-bot-jev}}"
RENDER=1; SKIP_NODE=0; DOCTOR=1
for a in "$@"; do case "$a" in
  --no-render) RENDER=0;; --skip-node) SKIP_NODE=1;; --no-doctor) DOCTOR=0;;
  -h|--help) sed -n '2,4p' "$0"; exit 0;; *) echo "unknown option: $a"; exit 2;; esac; done
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[warn] %s\033[0m\n' "$*"; }
die() { printf '\033[31m[error] %s\033[0m\n' "$*"; exit 1; }
SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"
export DEBIAN_FRONTEND=noninteractive PATH="$HOME/.local/bin:$PATH"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO_ROOT="$(dirname "$SRC_DIR")"

# ---------- apt helpers: timeouts + fallback to only Debian/Ubuntu + NodeSource sources ----------
APT_OPTS=()
apt_update() {
  [ -n "${JEVOPUS_APT_UPDATED:-}" ] && return 0
  if timeout "${JEVOPUS_APT_TIMEOUT:-${FARM_APT_TIMEOUT:-180}}" $SUDO apt-get "${APT_OPTS[@]}" update -qq; then JEVOPUS_APT_UPDATED=1; return 0; fi
  warn "apt-get update failed/hung (often a broken third-party source); retrying with only Debian/Ubuntu + NodeSource"
  local parts; parts="$(mktemp -d /tmp/jevopus-apt.XXXX)"
  for f in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
    [ -f "$f" ] && grep -qE 'deb\.debian\.org|security\.debian\.org|debian\.org/debian|archive\.ubuntu\.com|security\.ubuntu\.com|ports\.ubuntu\.com|nodesource\.com' "$f" && cp "$f" "$parts/"
  done
  local main=/dev/null
  [ -f /etc/apt/sources.list ] && grep -qE '^deb .*(debian|ubuntu)' /etc/apt/sources.list && main=/etc/apt/sources.list
  APT_OPTS=(-o "Dir::Etc::sourceparts=$parts" -o "Dir::Etc::sourcelist=$main")
  timeout 180 $SUDO apt-get "${APT_OPTS[@]}" update -qq && JEVOPUS_APT_UPDATED=1
}
apt_install() {  # installs only missing packages
  local miss=(); for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || miss+=("$p"); done
  [ ${#miss[@]} -eq 0 ] && return 0
  command -v apt-get >/dev/null || { warn "no apt-get; install manually: ${miss[*]}"; return 1; }
  apt_update || warn "apt update still failing"
  timeout 900 $SUDO apt-get "${APT_OPTS[@]}" install -y -qq "${miss[@]}"
}
[ -n "${JEVOPUS_TEST_APT_UPDATE:-}" ] && { apt_update && echo "apt_update ok"; exit $?; }

# ---------- 1. Node >= 22 ----------
say "1/7 Node.js >= 22"
node_major() { node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'; }
NM="$(node_major)"; NM="${NM:-0}"
if [ "$SKIP_NODE" = 0 ] && [ "$NM" -lt 22 ]; then
  apt_install ca-certificates curl gnupg || die "cannot install curl/gnupg"
  $SUDO install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | $SUDO gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" | $SUDO tee /etc/apt/sources.list.d/nodesource.list >/dev/null
  unset JEVOPUS_APT_UPDATED; APT_OPTS=()
  apt_update; timeout 900 $SUDO apt-get "${APT_OPTS[@]}" install -y -qq nodejs || die "Node 22 install failed"
fi
echo "node $(node -v 2>/dev/null || echo missing)"

# ---------- 2. worker CLIs: Codex and Claude Code ----------
say "2/7 worker CLIs (Codex, Claude Code)"
if ! command -v codex >/dev/null; then
  pfx="$(npm prefix -g 2>/dev/null)"
  if [ -w "$pfx/lib" ] || [ -w "$pfx" ]; then npm install -g @openai/codex
  elif [ -n "$SUDO" ]; then $SUDO npm install -g @openai/codex
  else npm config set prefix "$HOME/.local" && npm install -g @openai/codex; fi
fi
command -v codex >/dev/null && echo "codex $(codex --version 2>/dev/null)" || warn "codex not installed"
if command -v claude >/dev/null; then echo "claude $(claude --version 2>/dev/null)"
else echo "Claude Code CLI not installed yet: npm install -g @anthropic-ai/claude-code  (then: jevopus.py setup-seat claude-strong)"; fi

# ---------- 3. Jev router (grok-bot-jev preferred; muse-jev-playbook compatible) ----------
say "3/7 Jev router"
PARENT="$(dirname "$JEVOPUS_HOME")"
if [ -z "${JEVOPUS_JEV_DIR:-}" ]; then
  for d in "$REPO_ROOT" "$PARENT/grok-bot-jev" /workspace/grok-bot-jev "$PARENT/muse-jev-playbook" /workspace/muse-jev-playbook; do
    [ -f "$d/src/cli.py" ] && { JEVOPUS_JEV_DIR="$d"; break; }; done
  JEVOPUS_JEV_DIR="${JEVOPUS_JEV_DIR:-$PARENT/grok-bot-jev}"
fi
if [ ! -f "$JEVOPUS_JEV_DIR/src/cli.py" ]; then
  command -v git >/dev/null || apt_install git
  timeout 300 git clone -q --depth 1 "$JEV_URL" "$JEVOPUS_JEV_DIR" || die "git clone $JEV_URL failed"
fi
echo "jev repo: $JEVOPUS_JEV_DIR"
if [ ! -x "$JEVOPUS_JEV_DIR/.venv/bin/python" ]; then
  python3 -m venv "$JEVOPUS_JEV_DIR/.venv" 2>/dev/null || { apt_install python3-venv && python3 -m venv "$JEVOPUS_JEV_DIR/.venv"; } || die "python venv failed"
fi
"$JEVOPUS_JEV_DIR/.venv/bin/python" -c "import typesafe_sdk, yaml" 2>/dev/null || \
  timeout 600 "$JEVOPUS_JEV_DIR/.venv/bin/pip" install -q -r "$JEVOPUS_JEV_DIR/requirements.txt" || die "pip install (typesafe-sdk, pyyaml) failed"
if [ ! -f "$JEVOPUS_JEV_DIR/config.yaml" ]; then
  cp "$JEVOPUS_JEV_DIR/config.example.yaml" "$JEVOPUS_JEV_DIR/config.yaml"
  sed -i -E 's/^mode:.*/mode: shadow/' "$JEVOPUS_JEV_DIR/config.yaml"
fi
grep -q '^mode:' "$JEVOPUS_JEV_DIR/config.yaml" || echo 'mode: shadow' >> "$JEVOPUS_JEV_DIR/config.yaml"
echo "config: $JEVOPUS_JEV_DIR/config.yaml ($(grep -E '^mode:' "$JEVOPUS_JEV_DIR/config.yaml"))"

# ---------- 4. Jevopus files ----------
say "4/7 Jevopus files -> $JEVOPUS_HOME"
mkdir -p "$JEVOPUS_HOME"/{jobs,logs,seats,tools}
[ "$(cd "$JEVOPUS_HOME" && pwd)" = "$SRC_DIR" ] || (cd "$SRC_DIR" && tar -cf - jevopus.py README.md tools/resume_fix.sh jevopuslib/__init__.py jevopuslib/core.py jevopuslib/jev.py jevopuslib/reports.py jevopuslib/runner.py jevopuslib/setup.py recipes/assets/motion_reference.py recipes/code-review.json recipes/data-cleanup.json recipes/landing-page.json recipes/motion-video.json recipes/research-brief.json recipes/x-post-drafts.json | tar -xf - -C "$JEVOPUS_HOME") || die "copy failed"
chmod +x "$JEVOPUS_HOME/jevopus.py" "$JEVOPUS_HOME/tools/resume_fix.sh"
echo "$JEVOPUS_JEV_DIR" > "$JEVOPUS_HOME/.jev_dir"
echo "installed Jevopus v2.0.20260926 ($(find "$JEVOPUS_HOME/jevopuslib" "$JEVOPUS_HOME/recipes" -type f | wc -l) lib/recipe files); config.json, jevopus.db, jobs/ and seats/ are kept"

# ---------- 5. render venv (optional) ----------
say "5/7 render venv (Pillow, numpy, pycairo)"
RPY="$JEVOPUS_HOME/tools/render-venv/bin/python"
if [ "$RENDER" = 1 ]; then
  if ! "$RPY" -c "import PIL, numpy, cairo" 2>/dev/null; then
    apt_install libcairo2-dev pkg-config python3-dev python3-venv ffmpeg fontconfig fonts-dejavu-core || warn "some render packages missing"
    [ -x "$RPY" ] || python3 -m venv "$JEVOPUS_HOME/tools/render-venv"
    timeout 900 "$JEVOPUS_HOME/tools/render-venv/bin/pip" install -q Pillow numpy pycairo || warn "render venv pip install failed"
  fi
  "$RPY" -c "import PIL, numpy, cairo; print('render venv ok')" || warn "render venv not usable"
else echo "skipped (--no-render)"; fi

# ---------- 6. database ----------
say "6/7 jevopus.db (all seats start DISABLED; each is enabled only after setup-seat passes a live test)"
"$JEVOPUS_JEV_DIR/.venv/bin/python" "$JEVOPUS_HOME/jevopus.py" init

# ---------- 7. doctor ----------
say "7/7 doctor"
[ "$DOCTOR" = 1 ] && "$JEVOPUS_JEV_DIR/.venv/bin/python" "$JEVOPUS_HOME/jevopus.py" doctor
cat <<EOM

Next: set up the seat(s) you want (Codex and/or Claude Code); each is enabled after a live test:
  python3 $JEVOPUS_HOME/jevopus.py setup-seat codex-sub     # Codex on a ChatGPT subscription (device login), re-run to test+enable
  python3 $JEVOPUS_HOME/jevopus.py setup-seat claude-strong # Claude Code on a Claude login (prints login steps), re-run to test+enable
  python3 $JEVOPUS_HOME/jevopus.py setup-seat codex-api     # Codex on an OpenAI API key (JEVOPUS_OPENAI_API_KEY / OPENAI_API_KEY)
  python3 $JEVOPUS_HOME/jevopus.py recipes
EOM
exit 0
