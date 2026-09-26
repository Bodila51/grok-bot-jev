#!/usr/bin/env bash
# Farm installer v2.0.20260926 (from a repo clone). Idempotent; safe to re-run. Never prints secrets.
# usage: [FARM_HOME=/workspace/farm] [FARM_JEV_DIR=...] bash farm/install.sh [--no-render] [--skip-node] [--no-doctor]
set -uo pipefail
FARM_HOME="${FARM_HOME:-/workspace/farm}"
JEV_URL="${FARM_JEV_REPO:-https://github.com/Bodila51/grok-bot-jev}"
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
  [ -n "${FARM_APT_UPDATED:-}" ] && return 0
  if timeout "${FARM_APT_TIMEOUT:-180}" $SUDO apt-get "${APT_OPTS[@]}" update -qq; then FARM_APT_UPDATED=1; return 0; fi
  warn "apt-get update failed/hung (often a broken third-party source); retrying with only Debian/Ubuntu + NodeSource"
  local parts; parts="$(mktemp -d /tmp/farm-apt.XXXX)"
  for f in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
    [ -f "$f" ] && grep -qE 'deb\.debian\.org|security\.debian\.org|debian\.org/debian|archive\.ubuntu\.com|security\.ubuntu\.com|ports\.ubuntu\.com|nodesource\.com' "$f" && cp "$f" "$parts/"
  done
  local main=/dev/null
  [ -f /etc/apt/sources.list ] && grep -qE '^deb .*(debian|ubuntu)' /etc/apt/sources.list && main=/etc/apt/sources.list
  APT_OPTS=(-o "Dir::Etc::sourceparts=$parts" -o "Dir::Etc::sourcelist=$main")
  timeout 180 $SUDO apt-get "${APT_OPTS[@]}" update -qq && FARM_APT_UPDATED=1
}
apt_install() {  # installs only missing packages
  local miss=(); for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || miss+=("$p"); done
  [ ${#miss[@]} -eq 0 ] && return 0
  command -v apt-get >/dev/null || { warn "no apt-get; install manually: ${miss[*]}"; return 1; }
  apt_update || warn "apt update still failing"
  timeout 900 $SUDO apt-get "${APT_OPTS[@]}" install -y -qq "${miss[@]}"
}
[ -n "${FARM_TEST_APT_UPDATE:-}" ] && { apt_update && echo "apt_update ok"; exit $?; }

# ---------- 1. Node >= 22 ----------
say "1/7 Node.js >= 22"
node_major() { node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'; }
NM="$(node_major)"; NM="${NM:-0}"
if [ "$SKIP_NODE" = 0 ] && [ "$NM" -lt 22 ]; then
  apt_install ca-certificates curl gnupg || die "cannot install curl/gnupg"
  $SUDO install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | $SUDO gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" | $SUDO tee /etc/apt/sources.list.d/nodesource.list >/dev/null
  unset FARM_APT_UPDATED; APT_OPTS=()
  apt_update; timeout 900 $SUDO apt-get "${APT_OPTS[@]}" install -y -qq nodejs || die "Node 22 install failed"
fi
echo "node $(node -v 2>/dev/null || echo missing)"

# ---------- 2. Codex CLI ----------
say "2/7 Codex CLI"
if ! command -v codex >/dev/null; then
  pfx="$(npm prefix -g 2>/dev/null)"
  if [ -w "$pfx/lib" ] || [ -w "$pfx" ]; then npm install -g @openai/codex
  elif [ -n "$SUDO" ]; then $SUDO npm install -g @openai/codex
  else npm config set prefix "$HOME/.local" && npm install -g @openai/codex; fi
fi
command -v codex >/dev/null && codex --version || warn "codex not installed"
echo "Claude Code is optional: npm install -g @anthropic-ai/claude-code  (then: farm.py setup-seat claude-strong)"

# ---------- 3. Jev router (grok-bot-jev preferred; muse-jev-playbook compatible) ----------
say "3/7 Jev router"
PARENT="$(dirname "$FARM_HOME")"
if [ -z "${FARM_JEV_DIR:-}" ]; then
  for d in "$REPO_ROOT" "$PARENT/grok-bot-jev" /workspace/grok-bot-jev "$PARENT/muse-jev-playbook" /workspace/muse-jev-playbook; do
    [ -f "$d/src/cli.py" ] && { FARM_JEV_DIR="$d"; break; }; done
  FARM_JEV_DIR="${FARM_JEV_DIR:-$PARENT/grok-bot-jev}"
fi
if [ ! -f "$FARM_JEV_DIR/src/cli.py" ]; then
  command -v git >/dev/null || apt_install git
  timeout 300 git clone -q --depth 1 "$JEV_URL" "$FARM_JEV_DIR" || die "git clone $JEV_URL failed"
fi
echo "jev repo: $FARM_JEV_DIR"
if [ ! -x "$FARM_JEV_DIR/.venv/bin/python" ]; then
  python3 -m venv "$FARM_JEV_DIR/.venv" 2>/dev/null || { apt_install python3-venv && python3 -m venv "$FARM_JEV_DIR/.venv"; } || die "python venv failed"
fi
"$FARM_JEV_DIR/.venv/bin/python" -c "import typesafe_sdk, yaml" 2>/dev/null || \
  timeout 600 "$FARM_JEV_DIR/.venv/bin/pip" install -q -r "$FARM_JEV_DIR/requirements.txt" || die "pip install (typesafe-sdk, pyyaml) failed"
if [ ! -f "$FARM_JEV_DIR/config.yaml" ]; then
  cp "$FARM_JEV_DIR/config.example.yaml" "$FARM_JEV_DIR/config.yaml"
  sed -i -E 's/^mode:.*/mode: shadow/' "$FARM_JEV_DIR/config.yaml"
fi
grep -q '^mode:' "$FARM_JEV_DIR/config.yaml" || echo 'mode: shadow' >> "$FARM_JEV_DIR/config.yaml"
echo "config: $FARM_JEV_DIR/config.yaml ($(grep -E '^mode:' "$FARM_JEV_DIR/config.yaml"))"

# ---------- 4. Farm files ----------
say "4/7 Farm files -> $FARM_HOME"
mkdir -p "$FARM_HOME"/{jobs,logs,seats,tools}
[ "$(cd "$FARM_HOME" && pwd)" = "$SRC_DIR" ] || (cd "$SRC_DIR" && tar -cf - farm.py README.md tools/resume_fix.sh farmlib/__init__.py farmlib/core.py farmlib/jev.py farmlib/reports.py farmlib/runner.py farmlib/setup.py recipes/assets/motion_reference.py recipes/code-review.json recipes/data-cleanup.json recipes/landing-page.json recipes/motion-video.json recipes/research-brief.json recipes/x-post-drafts.json | tar -xf - -C "$FARM_HOME") || die "copy failed"
chmod +x "$FARM_HOME/farm.py" "$FARM_HOME/tools/resume_fix.sh"
echo "$FARM_JEV_DIR" > "$FARM_HOME/.jev_dir"
echo "installed Farm v2.0.20260926 ($(find "$FARM_HOME/farmlib" "$FARM_HOME/recipes" -type f | wc -l) lib/recipe files); config.json, farm.db, jobs/ and seats/ are kept"

# ---------- 5. render venv (optional) ----------
say "5/7 render venv (Pillow, numpy, pycairo)"
RPY="$FARM_HOME/tools/render-venv/bin/python"
if [ "$RENDER" = 1 ]; then
  if ! "$RPY" -c "import PIL, numpy, cairo" 2>/dev/null; then
    apt_install libcairo2-dev pkg-config python3-dev python3-venv ffmpeg fontconfig fonts-dejavu-core || warn "some render packages missing"
    [ -x "$RPY" ] || python3 -m venv "$FARM_HOME/tools/render-venv"
    timeout 900 "$FARM_HOME/tools/render-venv/bin/pip" install -q Pillow numpy pycairo || warn "render venv pip install failed"
  fi
  "$RPY" -c "import PIL, numpy, cairo; print('render venv ok')" || warn "render venv not usable"
else echo "skipped (--no-render)"; fi

# ---------- 6. database ----------
say "6/7 farm.db (fresh seats are DISABLED until setup-seat passes a live test)"
"$FARM_JEV_DIR/.venv/bin/python" "$FARM_HOME/farm.py" init

# ---------- 7. doctor ----------
say "7/7 doctor"
[ "$DOCTOR" = 1 ] && "$FARM_JEV_DIR/.venv/bin/python" "$FARM_HOME/farm.py" doctor
cat <<EOM

Next:
  python3 $FARM_HOME/farm.py setup-seat codex-sub     # ChatGPT subscription (device login), then re-run to test+enable
  python3 $FARM_HOME/farm.py setup-seat codex-api     # optional: OpenAI API key from FARM_OPENAI_API_KEY/OPENAI_API_KEY
  python3 $FARM_HOME/farm.py setup-seat claude-strong # optional Claude
  python3 $FARM_HOME/farm.py recipes
EOM
exit 0
