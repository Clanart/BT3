### Title
Unbounded regex evaluation in hookify rule engine allows recurring CPU-exhaustion (ReDoS) griefing of every subsequent tool call — ([File: plugins/hookify/core/rule_engine.py])

### Summary
The `AutoRange` report describes an attacker who plants a malicious, attacker-controlled callback (`onERC721Received`) that is invoked automatically on every future qualifying trigger, burns all available gas, and reverts — repeatedly griefing the bot and never getting cleaned up because cleanup only happens on successful completion. The `hookify` plugin has the same structural shape: an attacker/untrusted contributor plants an attacker-controlled artifact (a regex `pattern` in a `.claude/hookify.*.local.md` rule file) that is *automatically re-invoked on every subsequent qualifying event* (`Bash`, `Edit`, `Write`, `Stop`, `UserPromptSubmit`), and the evaluation of that artifact is unbounded — Python's `re` module has no timeout — so a catastrophic-backtracking pattern can hang CPU on every single matching tool call for the rest of the session, with no automatic removal mechanism.

### Finding Description
`RuleEngine._regex_match` compiles and executes attacker-supplied regex patterns directly against attacker/session-controlled text (`command`, `new_text`, `content`, etc.) with no complexity bound, size cap, or timeout: [1](#0-0) 

These patterns come straight from `.claude/hookify.*.local.md` files loaded via `load_rules()`/`load_rule_file()`, which parse arbitrary user-authored frontmatter without any pattern-safety validation (no length limit, no ReDoS static check, no timeout enforcement around `regex.search`): [2](#0-1) [3](#0-2) 

The engine is invoked on **every** `PreToolUse`, `Stop`, and `UserPromptSubmit` event for matching tools, meaning a single planted rule file re-triggers the vulnerable regex evaluation continuously for the remainder of the session — mirroring the "recurring, uncleanable" nature of the `AutoRange` griefing (config only removed on success/never in the attack path): [4](#0-3) 

Documentation confirms rule files are user-writable, dynamically re-read on every tool use, and only *recommended* (not enforced) to be gitignored — so a malicious or compromised contributor to a shared repo can commit a poisoned rule file that grief-DoSes every teammate who has the `hookify` plugin active: [5](#0-4) 

The `try/except/finally: sys.exit(0)` wrapper in every hook entry point means the hook always "completes" from Claude Code's perspective (fail-open on exceptions), but this does **not** protect against a hang inside `re.search` — a catastrophic-backtracking regex does not raise an exception, it just burns CPU until the process is killed by the hook's `timeout` setting (if any) or hangs the tool call: [6](#0-5) 

### Impact Explanation
Each qualifying tool call (every `Bash` command, `Edit`/`Write`, prompt submission, or stop attempt) re-triggers `load_rules()` + `evaluate_rules()` against the poisoned rule. A crafted pattern like `(a+)+$` matched against attacker-influenceable text (e.g., a long file path or command string) causes catastrophic backtracking, stalling that tool call for the hook's timeout window (or indefinitely if no timeout is configured) on every single subsequent matching action — a recurring, session-wide degradation of the agent's productivity, exactly analogous to the `AutoRange` bot being griefed on every triggering condition with no way to purge the offending config short of finding and deleting the file. Unlike a one-off DoS, this persists across the entire session/workspace lifetime because there is no automatic disable/removal of a rule that times out or fails.

### Likelihood Explanation
Requires only filesystem write access to `.claude/hookify.<name>.local.md` inside the project — the same "unprivileged self-service configuration" precondition as `AutoRange::configToken()` in the original report (any user/attacker can create this file for themselves, or a malicious contributor can commit one to a shared repo since gitignoring is only a recommendation, not enforced). No special permissions, no bypass of authorization is needed — this is a workspace/hook-bypass style trust-boundary issue where the "operator" (the interactive Claude Code session/bot analog) pays the recurring cost.

### Recommendation
- Enforce a hard timeout around every `regex.search()` call in `_regex_match` (e.g., via a worker thread/process with `SIGALRM` or `regex` module with timeout support), independent of the outer hook process timeout.
- Cap input length before regex evaluation and reject/flag patterns with static ReDoS heuristics (nested quantifiers, catastrophic backtracking detection) at rule-load time.
- Validate rule files at load time and disable (with a warning) any rule whose pattern exceeds a safe complexity/runtime budget, persisting that disablement so it does not re-trigger the CPU-exhaustion on every subsequent tool call.

### Proof of Concept
1. Attacker (any user/contributor with write access to the project) creates `.claude/hookify.grief.local.md`:
```markdown
---
name: grief
enabled: true
event: bash
pattern: ^(a+)+$
---
Triggered
```
2. On the next `Bash` tool call whose `command` field is crafted to end in a long run of `a` characters followed by a non-matching character (e.g., `echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaab`), `pretooluse.py` calls `RuleEngine.evaluate_rules` → `_regex_match` → `re.search(r'^(a+)+$', command)`, which exhibits catastrophic backtracking and hangs.
3. Because the rule file persists in `.claude/`, **every** subsequent `Bash` invocation whose command text can be shaped to trigger the pattern re-triggers the hang — a recurring resource-exhaustion DoS with no automatic cleanup, matching the "protocol can be repeatedly gas griefed" pattern of the source report.

### Citations

**File:** plugins/hookify/core/rule_engine.py (L256-273)
```python
    def _regex_match(self, pattern: str, text: str) -> bool:
        """Check if pattern matches text using regex.

        Args:
            pattern: Regex pattern
            text: Text to match against

        Returns:
            True if pattern matches
        """
        try:
            # Use cached compiled regex (LRU cache with max 128 patterns)
            regex = compile_regex(pattern)
            return bool(regex.search(text))

        except re.error as e:
            print(f"Invalid regex pattern '{pattern}': {e}", file=sys.stderr)
            return False
```

**File:** plugins/hookify/core/config_loader.py (L198-241)
```python
def load_rules(event: Optional[str] = None) -> List[Rule]:
    """Load all hookify rules from .claude directory.

    Args:
        event: Optional event filter ("bash", "file", "stop", etc.)

    Returns:
        List of enabled Rule objects matching the event.
    """
    rules = []

    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)

    for file_path in files:
        try:
            rule = load_rule_file(file_path)
            if not rule:
                continue

            # Filter by event if specified
            if event:
                if rule.event != 'all' and rule.event != event:
                    continue

            # Only include enabled rules
            if rule.enabled:
                rules.append(rule)

        except (IOError, OSError, PermissionError) as e:
            # File I/O errors - log and continue
            print(f"Warning: Failed to read {file_path}: {e}", file=sys.stderr)
            continue
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            # Parsing errors - log and continue
            print(f"Warning: Failed to parse {file_path}: {e}", file=sys.stderr)
            continue
        except Exception as e:
            # Unexpected errors - log with type details
            print(f"Warning: Unexpected error loading {file_path} ({type(e).__name__}): {e}", file=sys.stderr)
            continue

    return rules
```

**File:** plugins/hookify/core/config_loader.py (L244-274)
```python
def load_rule_file(file_path: str) -> Optional[Rule]:
    """Load a single rule file.

    Returns:
        Rule object or None if file is invalid.
    """
    try:
        with open(file_path, 'r') as f:
            content = f.read()

        frontmatter, message = extract_frontmatter(content)

        if not frontmatter:
            print(f"Warning: {file_path} missing YAML frontmatter (must start with ---)", file=sys.stderr)
            return None

        rule = Rule.from_dict(frontmatter, message)
        return rule

    except (IOError, OSError, PermissionError) as e:
        print(f"Error: Cannot read {file_path}: {e}", file=sys.stderr)
        return None
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        print(f"Error: Malformed rule file {file_path}: {e}", file=sys.stderr)
        return None
    except UnicodeDecodeError as e:
        print(f"Error: Invalid encoding in {file_path}: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: Unexpected error parsing {file_path} ({type(e).__name__}): {e}", file=sys.stderr)
        return None
```

**File:** plugins/hookify/hooks/pretooluse.py (L35-60)
```python
def main():
    """Main entry point for PreToolUse hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Determine event type for filtering
        # For PreToolUse, we use tool_name to determine "bash" vs "file" event
        tool_name = input_data.get('tool_name', '')

        event = None
        if tool_name == 'Bash':
            event = 'bash'
        elif tool_name in ['Edit', 'Write', 'MultiEdit']:
            event = 'file'

        # Load rules
        rules = load_rules(event=event)

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)

```

**File:** plugins/hookify/hooks/pretooluse.py (L61-70)
```python
    except Exception as e:
        # On any error, allow the operation and log
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0 - never block operations due to hook errors
        sys.exit(0)
```

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L283-309)
```markdown
## File Organization

**Location:** All rules in `.claude/` directory
**Naming:** `.claude/hookify.{descriptive-name}.local.md`
**Gitignore:** Add `.claude/*.local.md` to `.gitignore`

**Good names:**
- `hookify.dangerous-rm.local.md`
- `hookify.console-log.local.md`
- `hookify.require-tests.local.md`
- `hookify.sensitive-files.local.md`

**Bad names:**
- `hookify.rule1.local.md` (not descriptive)
- `hookify.md` (missing .local)
- `danger.local.md` (missing hookify prefix)

## Workflow

### Creating a Rule

1. Identify unwanted behavior
2. Determine which tool is involved (Bash, Edit, etc.)
3. Choose event type (bash, file, stop, etc.)
4. Write regex pattern
5. Create `.claude/hookify.{name}.local.md` file in project root
6. Test immediately - rules are read dynamically on next tool use
```
