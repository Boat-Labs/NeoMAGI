#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/devcoord/check_skill_activation.sh <skill-name> <prompt...>

Examples:
  scripts/devcoord/check_skill_activation.sh devcoord-tester \
    "Use the devcoord-tester skill for this repository. After gate-review, what exact next-step behavior is required?"

  DISABLE_SLASH_COMMANDS=1 scripts/devcoord/check_skill_activation.sh devcoord-tester \
    "You are acting as the tester teammate in this repository. The review report for the current gate has just been committed and is visible in the main repo. What should you do next?"
EOF
}

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 64
fi

skill_name="$1"
shift
prompt="$*"
log_file="$(mktemp -t "${skill_name}.claude")"

cmd=(claude -p --no-session-persistence --debug-file "$log_file")
if [[ "${DISABLE_SLASH_COMMANDS:-0}" == "1" ]]; then
  cmd+=(--disable-slash-commands)
fi
cmd+=("$prompt")

output="$("${cmd[@]}")"

printf '%s\n' "$output"
printf '\n'
printf 'debug_log=%s\n' "$log_file"

rg -n \
  -e "Loading skills from" \
  -e "Loaded .*skills" \
  -e "Metadata string for ${skill_name}:" \
  -e "processPromptSlashCommand creating .* ${skill_name}" \
  -e "SkillTool returning .* skill ${skill_name}" \
  "$log_file" || true
