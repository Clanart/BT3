### Title
Unquoted `$ARGUMENTS` substitution in `!` bash execution block enables command injection beyond `allowed-tools` scope - (File: `plugins/ralph-wiggum/commands/ralph-loop.md`)

### Finding Description
The frontmatter restricts tool usage to `Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh:*)` [1](#0-0) , but the executable block itself builds a raw shell command line by directly, unquoted, concatenating `$ARGUMENTS` after the script path:

```
"${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh" $ARGUMENTS
``` [2](#0-1) 

`$ARGUMENTS` is a straight textual substitution of whatever the user typed after `/ralph-loop`, performed before the string is handed to a shell for execution. Because it is not quoted (e.g. `"$ARGUMENTS"`) and not passed as a pre-split argv array, any shell metacharacters in the user-supplied text (`;`, `` ` ``, `$(...)`, `&&`, newline) become live shell syntax once the composed line is executed. For example, invoking `/ralph-loop foo; touch /tmp/pwned` produces the literal command line:

```
"/path/to/plugin/scripts/setup-ralph-loop.sh" foo; touch /tmp/pwned
```

which a shell will interpret as two separate commands — the intended script invocation followed by an attacker-controlled `touch /tmp/pwned`. This is consistent with the plugin/command documentation elsewhere in the repo, which explicitly instructs command authors to always quote and validate variables inserted into bash execution blocks and warns that bash execution blocks are shell-interpreted before Claude ever sees them [3](#0-2) , and independently documents that `allowed-tools: Bash(<prefix>:*)` filters are prefix/command based and are recommended specifically as a security control against unintended command execution [4](#0-3) . The `setup-ralph-loop.sh` script's own argument parser only receives argv elements after shell word-splitting/expansion has already occurred, so it can never see or block a shell-level injection that happens before the script is even invoked [5](#0-4) . The `allowed-tools` restriction only constrains which command *prefix* is permitted (`.../setup-ralph-loop.sh`); it does not perform full shell-syntax parsing to catch subsequent chained/injected commands appended via metacharacters, so the invariant "deny means deny" for the tool scope is not actually enforced against this class of injection at the markdown-authoring level.

### Impact Explanation
If the substitution mechanism executes the composed line through a real shell (as the unquoted interpolation implies), an attacker who controls only the `/ralph-loop` argument text can achieve arbitrary command execution outside the sanctioned `setup-ralph-loop.sh` script — e.g., writing files, exfiltrating data, or modifying the repository/workspace, which is a direct violation of the plugin's declared `allowed-tools` scope and constitutes unauthorized command/file action within the Claude Code trust boundary.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs to supply the `$ARGUMENTS` text to a `/ralph-loop` invocation (e.g., via a crafted PR description, issue text, or any automation that feeds untrusted text into this slash command's arguments) — no shell access or elevated privilege is required. The vulnerable pattern is a straightforward, repeatable string-concatenation flaw present in the checked-in markdown file itself, not a hypothetical parser edge case.

### Recommendation
Do not interpolate `$ARGUMENTS` unquoted into a bash command template. Quote it (`"${ARGUMENTS}"`) at minimum, and prefer passing arguments as a properly-escaped, single opaque string, or restructure so the setup script receives raw argv without any intermediate shell re-parsing of attacker-controlled text. Additionally, harden `setup-ralph-loop.sh` and the command-invocation layer to treat `$ARGUMENTS`/positional args as data, never as shell syntax.

### Proof of Concept
Integration test plan:
1. Install/invoke the `ralph-loop` command with `$ARGUMENTS = "foo; touch /tmp/pwned"` (or `foo $(touch /tmp/pwned)` / backtick variant).
2. Assert that `/tmp/pwned` is **not** created and that only `setup-ralph-loop.sh` executed with `foo` (and the literal injected text) as inert data, not as executed shell syntax.
3. Failing assertion (i.e., `/tmp/pwned` exists) confirms the command-injection bypass of the `allowed-tools` scope.

Note: I could not directly inspect the closed-source Claude Code CLI's exact preprocessing/execution implementation for the `!` bash-execution block (it is not part of this repository), so I cannot 100% confirm whether the CLI additionally shell-escapes `$ARGUMENTS` before substitution or executes the resulting string via `sh -c`/`system()`-style invocation. This finding is based on the unquoted-concatenation pattern visible in the checked-in command file and corroborated by the repo's own documentation, which explicitly treats these bash blocks as literal shell command strings and advises quoting/validation as a security best practice.

### Citations

**File:** plugins/ralph-wiggum/commands/ralph-loop.md (L4-4)
```markdown
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh:*)"]
```

**File:** plugins/ralph-wiggum/commands/ralph-loop.md (L12-14)
```markdown
```!
"${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh" $ARGUMENTS
```
```

**File:** plugins/plugin-dev/skills/command-development/references/testing-strategies.md (L246-289)
```markdown
### Level 6: Bash Execution Testing

**What to test:**
- !` commands execute correctly
- Command output included in prompt
- Command failures handled
- Security: only allowed commands run

**Test procedure:**

```bash
# Create test command with bash execution
cat > .claude/commands/test-bash.md << 'EOF'
---
description: Test bash execution
allowed-tools: Bash(echo:*), Bash(date:*)
---

Current date: !`date`
Test output: !`echo "Hello from bash"`

Analysis of output above...
EOF

# Test in Claude Code
> /test-bash
# Verify:
# 1. Date appears correctly
# 2. Echo output appears
# 3. No errors in debug logs

# Test with disallowed command (should fail or be blocked)
cat > .claude/commands/test-forbidden.md << 'EOF'
---
description: Test forbidden command
allowed-tools: Bash(echo:*)
---

Trying forbidden: !`ls -la /`
EOF

> /test-forbidden
# Verify: Permission denied or appropriate error
```
```

**File:** plugins/plugin-dev/skills/command-development/references/frontmatter-reference.md (L107-128)
```markdown
**When to use:**

1. **Security:** Restrict command to safe operations
   ```yaml
   allowed-tools: Read, Grep  # Read-only command
   ```

2. **Clarity:** Document required tools
   ```yaml
   allowed-tools: Bash(git:*), Read
   ```

3. **Bash execution:** Enable bash command output
   ```yaml
   allowed-tools: Bash(git status:*), Bash(git diff:*)
   ```

**Best practices:**
- Be as restrictive as possible
- Use command filters for Bash (e.g., `git:*` not `*`)
- Only specify when different from conversation permissions
- Document why specific tools are needed
```

**File:** plugins/ralph-wiggum/scripts/setup-ralph-loop.sh (L8-110)
```shellscript
# Parse arguments
PROMPT_PARTS=()
MAX_ITERATIONS=0
COMPLETION_PROMISE="null"

# Parse options and positional arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--help)
      cat << 'HELP_EOF'
Ralph Loop - Interactive self-referential development loop

USAGE:
  /ralph-loop [PROMPT...] [OPTIONS]

ARGUMENTS:
  PROMPT...    Initial prompt to start the loop (can be multiple words without quotes)

OPTIONS:
  --max-iterations <n>           Maximum iterations before auto-stop (default: unlimited)
  --completion-promise '<text>'  Promise phrase (USE QUOTES for multi-word)
  -h, --help                     Show this help message

DESCRIPTION:
  Starts a Ralph Wiggum loop in your CURRENT session. The stop hook prevents
  exit and feeds your output back as input until completion or iteration limit.

  To signal completion, you must output: <promise>YOUR_PHRASE</promise>

  Use this for:
  - Interactive iteration where you want to see progress
  - Tasks requiring self-correction and refinement
  - Learning how Ralph works

EXAMPLES:
  /ralph-loop Build a todo API --completion-promise 'DONE' --max-iterations 20
  /ralph-loop --max-iterations 10 Fix the auth bug
  /ralph-loop Refactor cache layer  (runs forever)
  /ralph-loop --completion-promise 'TASK COMPLETE' Create a REST API

STOPPING:
  Only by reaching --max-iterations or detecting --completion-promise
  No manual stop - Ralph runs infinitely by default!

MONITORING:
  # View current iteration:
  grep '^iteration:' .claude/ralph-loop.local.md

  # View full state:
  head -10 .claude/ralph-loop.local.md
HELP_EOF
      exit 0
      ;;
    --max-iterations)
      if [[ -z "${2:-}" ]]; then
        echo "❌ Error: --max-iterations requires a number argument" >&2
        echo "" >&2
        echo "   Valid examples:" >&2
        echo "     --max-iterations 10" >&2
        echo "     --max-iterations 50" >&2
        echo "     --max-iterations 0  (unlimited)" >&2
        echo "" >&2
        echo "   You provided: --max-iterations (with no number)" >&2
        exit 1
      fi
      if ! [[ "$2" =~ ^[0-9]+$ ]]; then
        echo "❌ Error: --max-iterations must be a positive integer or 0, got: $2" >&2
        echo "" >&2
        echo "   Valid examples:" >&2
        echo "     --max-iterations 10" >&2
        echo "     --max-iterations 50" >&2
        echo "     --max-iterations 0  (unlimited)" >&2
        echo "" >&2
        echo "   Invalid: decimals (10.5), negative numbers (-5), text" >&2
        exit 1
      fi
      MAX_ITERATIONS="$2"
      shift 2
      ;;
    --completion-promise)
      if [[ -z "${2:-}" ]]; then
        echo "❌ Error: --completion-promise requires a text argument" >&2
        echo "" >&2
        echo "   Valid examples:" >&2
        echo "     --completion-promise 'DONE'" >&2
        echo "     --completion-promise 'TASK COMPLETE'" >&2
        echo "     --completion-promise 'All tests passing'" >&2
        echo "" >&2
        echo "   You provided: --completion-promise (with no text)" >&2
        echo "" >&2
        echo "   Note: Multi-word promises must be quoted!" >&2
        exit 1
      fi
      COMPLETION_PROMISE="$2"
      shift 2
      ;;
    *)
      # Non-option argument - collect all as prompt parts
      PROMPT_PARTS+=("$1")
      shift
      ;;
  esac
done
```
