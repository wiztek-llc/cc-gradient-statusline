#!/bin/bash
# UserPromptSubmit hook: inject the current date/time into context at the start
# of every turn. For UserPromptSubmit, plain stdout is added to the model's
# context. Also seeds the shared throttle file so the PostToolUse time hook
# won't redundantly re-inject right after a prompt.
STATE="${TMPDIR:-/tmp}/cc-time-last"
date +%s > "$STATE" 2>/dev/null
printf 'Current date/time: %s' "$(date '+%A, %Y-%m-%d %H:%M %Z')"
