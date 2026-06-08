#!/bin/bash
# PostToolUse hook: keep the model time-aware DURING long tasks. Fires after
# every tool call, but throttles to one injection per CC_TIME_INTERVAL seconds
# so context isn't flooded. Emits hookSpecificOutput.additionalContext (the
# structured form PostToolUse requires to inject into context).
STATE="${TMPDIR:-/tmp}/cc-time-last"
INTERVAL="${CC_TIME_INTERVAL:-60}"
NOW=$(date +%s)
LAST=$(cat "$STATE" 2>/dev/null || echo 0)
case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
if [ $((NOW - LAST)) -ge "$INTERVAL" ]; then
  echo "$NOW" > "$STATE" 2>/dev/null
  TS=$(date '+%A, %Y-%m-%d %H:%M:%S %Z')
  printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"Current time is now %s"}}' "$TS"
fi
# else: no output -> no injection this tool call
