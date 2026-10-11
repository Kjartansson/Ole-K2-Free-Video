#!/usr/bin/env bash
# One command for the test-suite job (brief: docs/briefs/tests.md). Kimi builds tests/ in two short,
# fresh sessions; nothing is committed (Claude reviews and commits afterwards).
#   ./docs/briefs/run_tests_job.sh          both parts
#   ./docs/briefs/run_tests_job.sh 2        only part 2 (part 1 already done)
# Logs: logs/tests/kimi_tests_1.txt, kimi_tests_2.txt. Each Kimi session is capped at 25 minutes.
cd "$(dirname "$(readlink -f "$0")")/../.." || exit 1
mkdir -p logs/tests
command -v kimi >/dev/null || { echo "kimi is not installed on this PC"; exit 1; }

RULES="Batch your reads into one shell call (grep -n for the functions, then sed -n line ranges), no re-reads. \
Create files under tests/ only; never edit app/ or web/. If a failure is a real app bug, leave the test failing and \
name it. Do not commit. No narration, no intermediate messages, think silently."

run_part() {   # $1 = part number, $2 = what to do, $3 = final-output instruction
  local log="logs/tests/kimi_tests_$1.txt"
  echo "== Kimi part $1 (max 25 min) ... log: $log"
  timeout 1500 kimi -p "Work in $PWD. Read docs/briefs/tests.md and do ONLY $2. $RULES Your ONLY output: $3" \
    > "$log" 2>&1
  echo "exit $?" >> "$log"
  if grep -qiE "usage limit|quota will reset|provider.auth_error" "$log"; then
    echo "!! Kimi's usage quota is used up. Try again after it resets (it says when in $log)."
    exit 2
  fi
  tail -12 "$log" | cut -c1-240
}

if [ "${1:-1}" = "1" ]; then
  run_part 1 "'Isolation' and 'Part 1'. Run python3 tests/run_all.py 2>&1 | tail -25 and iterate until your tests are correct." \
    "max 10 lines: files created, test counts, the run_all result line, any real app bugs found."
fi
if [ -f tests/run_all.py ]; then
  run_part 2 "'Part 2' (Part 1 and tests/_isolation.py exist already; reuse them). Run python3 tests/run_all.py 2>&1 | tail -25 and iterate until your tests are correct." \
    "max 10 lines: files created, test counts, the run_all result line, any real app bugs found."
  echo "== Result of the whole suite:"
  timeout 300 python3 tests/run_all.py 2>&1 | tail -15
  echo "== Done. Ask Claude: 'review and commit the test suite' (Kimi does not commit)."
else
  echo "!! tests/run_all.py was not created; see logs/tests/kimi_tests_1.txt"
fi
