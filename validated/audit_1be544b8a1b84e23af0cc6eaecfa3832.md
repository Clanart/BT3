### Title
Sed frontmatter delimiter is naive to embedded '---' lines, allowing YAML scalar content to smuggle forged frontmatter fields - ([File: plugins/plugin-dev/skills/plugin-settings/scripts/parse-frontmatter.sh])

### Summary
`parse-frontmatter.sh` (and the identical inline pattern documented in `SKILL.md` and used by `plugins/ralph-wiggum/hooks/stop-hook.sh`) extracts YAML frontmatter using `sed -n '/^---$/,/^---$/{ /^---$/d; p; }'`, which treats any line that is exactly `---` as a delimiter regardless of YAML semantics. If the legitimate frontmatter block contains a literal `---` line embedded inside a multiline/block scalar value (e.g. a `|`-style YAML string), `sed` will close the "frontmatter" range at that embedded line instead of the real closing delimiter, and — because GNU `sed` re-opens a new range on the next `/^---$/` match — the *real* closing `---` line is reinterpreted as a new range start with no subsequent terminator, causing the entire markdown body to be absorbed into the `FRONTMATTER` variable.

### Finding Description
The extraction logic is:
```
FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$FILE")
``` [1](#0-0) 

This is a purely line-based scan with no YAML awareness of quoting or block scalars (`|`, `>`). If a field value in the legitimate frontmatter is written as a multiline block scalar containing a line that is exactly `---` (e.g. `additional_instructions: |\n  ---\n  more text\n`), the first `sed` range closes at that embedded `---`. Since the pattern's end-address match immediately re-triggers as a start-address match on the next `/^---$/`-matching line, the real closing `---` of the frontmatter block is treated as opening a brand-new (unterminated) range, and — absent a further triple-dash later in the file — everything from that point to end-of-file (i.e., the entire markdown body) is captured into `FRONTMATTER`.

Downstream field extraction (`grep "^${FIELD}:"` against `$FRONTMATTER`, as used identically in `plugins/ralph-wiggum/hooks/stop-hook.sh` lines 21-25) [2](#0-1)  would then match any line in the markdown body that happens to look like `field_name: value` (e.g. an example, a quoted snippet, or attacker-supplied text embedded in the body), letting body content override or forge fields such as `iteration:`, `max_iterations:`, `completion_promise:`, `enabled:`, `coordinator_session:`, etc.

For this to be exploitable, the attacker needs to control content that ends up written into the legitimate frontmatter block of a `.local.md` state file with an embedded `---`, or otherwise control the markdown body content once frontmatter parsing has been broken. `SKILL.md`'s own "Sanitize User Input" guidance only escapes double quotes when writing user-controlled values into settings [3](#0-2) , and does not address embedded newlines or `---` sequences in multiline scalars, so a plugin following this documented pattern to store an attacker-influenced field (e.g. `additional_instructions`) is left exposed to this delimiter-confusion issue.

### Impact Explanation
Within `plugins/ralph-wiggum/hooks/stop-hook.sh`, this parsing pattern controls loop-continuation/termination logic (`iteration`, `max_iterations`, `completion_promise`), which feeds back into the `Stop` hook decision (`"decision": "block"`) and constructs the prompt resubmitted to Claude [4](#0-3) . If a forged/mis-scoped field value could be smuggled in via this delimiter confusion, it could affect loop termination behavior or the prompt text fed back to the model. However, I could not find any real, shipped hook script (beyond `stop-hook.sh` and the purely illustrative `agent-stop-notification.sh` referenced only in documentation, which does not exist as an actual file in the repo) that reads security-critical, privilege-affecting fields (e.g., approval bypass, command allowlists) via this exact parser. The concrete, reachable impact within the current repository is limited to state-file/loop-control confusion in `ralph-wiggum`, not a demonstrated approval bypass, arbitrary command execution, or secret disclosure.

### Likelihood Explanation
Exploitation requires: (1) an attacker-influenced value being written into a `.local.md` frontmatter field as an unescaped multiline block scalar containing a literal `---` line, and (2) that the resulting corrupted "frontmatter" then contains a spoofable `field: value` line that a hook trusts. These `.local.md` files are documented as user-managed, git-ignored, per-project files created via legitimate commands/setup flows [5](#0-4) , so an unprivileged external attacker (e.g., via PR/issue text alone, with no write access to the local `.claude/` directory or ability to run commands) has no direct, demonstrated path in this repo to inject such content into an existing project's `.local.md` file. No commands/scripts in this repo were found that write attacker-controlled multiline text directly into frontmatter fields without any newline/`---` sanitization.

### Recommendation
Replace the naive `sed` range extraction with a YAML-aware parser (e.g., `yq`, or an `awk`/`python` script that tracks block-scalar indentation state) so that `---` lines occurring inside a scalar's indented block are not treated as document delimiters. At minimum, only treat a line as a frontmatter delimiter if it is un-indented (`^---$` with no leading whitespace) *and* the parser is not currently inside a recognized block scalar context; better, use `awk 'NR==1 && /^---$/{f=1;next} f && /^---$/{exit} f'` combined with strict single-line-scalar-only field policies, or fully delegate YAML parsing to `yq -r`.

### Proof of Concept
Integration test for `plugins/ralph-wiggum/hooks/stop-hook.sh` (and `parse-frontmatter.sh`):
1. Create `.claude/ralph-loop.local.md` with:
```
---
iteration: 1
max_iterations: 10
completion_promise: |
  ---
  enabled: true
---
# Body
max_iterations: 999
```
2. Run `FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' .claude/ralph-loop.local.md)` and assert that `echo "$FRONTMATTER" | grep '^max_iterations:'` returns `max_iterations: 999` (the body value) instead of the legitimate `max_iterations: 10`, demonstrating that body content overrides the intended frontmatter value.
3. Assert this behavior differs from a YAML-conformant parser (e.g. `yq '.max_iterations' file`), which correctly returns `10` and treats `---` inside the `completion_promise` block scalar as literal text.

### Citations

**File:** plugins/plugin-dev/skills/plugin-settings/scripts/parse-frontmatter.sh (L36-37)
```shellscript
# Extract frontmatter
FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$FILE")
```

**File:** plugins/ralph-wiggum/hooks/stop-hook.sh (L21-25)
```shellscript
FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$RALPH_STATE_FILE")
ITERATION=$(echo "$FRONTMATTER" | grep '^iteration:' | sed 's/iteration: *//')
MAX_ITERATIONS=$(echo "$FRONTMATTER" | grep '^max_iterations:' | sed 's/max_iterations: *//')
# Extract completion_promise and strip surrounding quotes if present
COMPLETION_PROMISE=$(echo "$FRONTMATTER" | grep '^completion_promise:' | sed 's/completion_promise: *//' | sed 's/^"\(.*\)"$/\1/')
```

**File:** plugins/ralph-wiggum/hooks/stop-hook.sh (L165-174)
```shellscript
# Output JSON to block the stop and feed prompt back
# The "reason" field contains the prompt that will be sent back to Claude
jq -n \
  --arg prompt "$PROMPT_TEXT" \
  --arg msg "$SYSTEM_MSG" \
  '{
    "decision": "block",
    "reason": $prompt,
    "systemMessage": $msg
  }'
```

**File:** plugins/plugin-dev/skills/plugin-settings/SKILL.md (L17-18)
```markdown
- Usage: Read from hooks, commands, and agents
- Lifecycle: User-managed (not in git, should be in `.gitignore`)
```

**File:** plugins/plugin-dev/skills/plugin-settings/SKILL.md (L387-401)
```markdown
### Sanitize User Input

When writing settings files from user input:

```bash
# Escape quotes in user input
SAFE_VALUE=$(echo "$USER_INPUT" | sed 's/"/\\"/g')

# Write to file
cat > "$STATE_FILE" <<EOF
---
user_setting: "$SAFE_VALUE"
---
EOF
```
```
