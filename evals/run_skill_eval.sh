#!/usr/bin/env bash
# Do real Claude Code sessions (a) load the right puenteo skill and (b) answer with correct commands?
# Usage: evals/run_skill_eval.sh [runs-per-case] [model]      (needs `claude` on PATH, skills installed)
# Cases: evals/skill_cases.tsv  →  <skill or NONE> \t <regex the answer must match> \t <prompt>
set -u
RUNS=${1:-1}; MODEL=${2:-haiku}
DIR="$(cd "$(dirname "$0")" && pwd)"
JUDGE='
import json, re, sys
want = sys.argv[1]; skills, text = [], []
for l in sys.stdin:
    try: o = json.loads(l)
    except Exception: continue
    if not isinstance(o, dict): continue
    if o.get("type") == "result" and isinstance(o.get("result"), str): text.append(o["result"])
    m = o.get("message")
    if isinstance(m, dict):
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                skills.append(str((b.get("input") or {}).get("skill")))
print(("CMD_OK" if re.search(want, "\n".join(text)) else "CMD_MISS"), ",".join(skills) or "-")
'
sk=0; cm=0; tot=0
while IFS=$'\t' read -r want rx prompt; do
  for _ in $(seq 1 "$RUNS"); do
    read -r cmd used < <(echo "$prompt" | claude -p ${CLAUDE_SETTINGS:+--settings "$CLAUDE_SETTINGS"} --model "$MODEL" \
      --output-format stream-json --verbose --allowedTools Skill 2>/dev/null | python3 -c "$JUDGE" "$rx")
    tot=$((tot + 1))
    if [ "$want" = NONE ]; then
      case "$used" in *puenteo*) s=✗ ;; *) s=✓; sk=$((sk + 1)); cm=$((cm + 1)) ;; esac
    else
      case ",$used," in *",$want,"*) s=✓; sk=$((sk + 1)) ;; *) s=· ;; esac
      [ "$cmd" = CMD_OK ] && cm=$((cm + 1))
    fi
    printf '%s %-22s %-8s skill=%s\n' "$s" "$want" "$cmd" "$used"
  done
done < "$DIR/skill_cases.tsv"
echo "answers with correct commands: $cm/$tot · right skill loaded (or none when unrelated): $sk/$tot"
