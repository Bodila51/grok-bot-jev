#!/bin/bash
# Manual follow-up on a codex session (jevopus.py run does this automatically for verification fixes).
# usage: resume_fix.sh <job_id> <session_id> <prompt_file> <image|-> <logname> [model=gpt-6-sol] [seat=codex-sub]
set -u; F=${JEVOPUS_HOME:-${FARM_HOME:-$(cd "$(dirname "$0")/.." && pwd)}}; J=$F/jobs/$1; M=${6:-gpt-6-sol}; S=${7:-codex-sub}
PY=python3; IMG=(); [ "$4" != "-" ] && IMG=(-i "$4")
$PY -c "import sqlite3;c=sqlite3.connect('$F/jevopus.db');c.execute(\"UPDATE seats SET status='busy' WHERE id='$S'\");c.commit()"
cd "$J" && env -u OPENAI_API_KEY -u JEVOPUS_OPENAI_API_KEY -u FARM_OPENAI_API_KEY -u ANTHROPIC_API_KEY -u TYPESAFE_API_KEY CODEX_HOME=$F/seats/$S/codex \
  timeout 3600 codex exec resume "$2" --skip-git-repo-check -m "$M" -c model_reasoning_effort=high \
  -c sandbox_mode=workspace-write "${IMG[@]}" "$(cat "$3")" > "$5" 2>&1 < /dev/null; rc=$?
limited=0; if [ $rc -ne 0 ] && grep -qiE "rate.?limit|usage limit|too many requests|insufficient_quota|limit reached" "$5"; then limited=1; fi
$PY - <<PY
import sqlite3,time,json,datetime,re
c=sqlite3.connect('$F/jevopus.db')
if $limited: c.execute("UPDATE seats SET status='cooldown', cooldown_until=? WHERE id='$S'",(time.time()+3600,)); c.execute("INSERT INTO seat_events VALUES(?,?,?,?,?,?)",(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),time.time(),'$S','cooldown','$1','resume_fix'))
else: c.execute("UPDATE seats SET status='idle' WHERE id='$S'")
t=sum(int(m.replace(',','')) for m in re.findall(r"tokens used[:\s]*\n?\s*([\d,]+)",open('$5',errors='ignore').read()))
c.execute("UPDATE jobs SET tokens=COALESCE(tokens,0)+? WHERE id='$1'",(t,)); c.commit()
open('$F/logs/routing.jsonl','a').write(json.dumps({"ts":datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),"event":"run_fix","job_id":"$1","seat":"$S","model":"$M","session":"$2","rc":$rc,"limited":bool($limited),"tokens":t})+"\n")
PY
echo rc=$rc limited=$limited
