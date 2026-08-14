### Title
Hookify `PreToolUse` hook fails open on import/parse errors, allowing dangerous Bash/Edit/Write/MultiEdit operations to proceed unchecked - (File: `plugins/hookify/hooks/pretooluse.py`)

### Summary
`plugins/hookify/hooks/pretooluse.py` is the enforcement point that is supposed to evaluate user-defined `.claude/hookify.*.local.md` rules before any `Bash`, `Edit`, `Write`, or `MultiEdit` tool call is allowed to run. Both the module-level import block and the `main()` function are written to catch any exception (including `ImportError`/`json.JSONDecodeError`/rule-engine bugs) and unconditionally `sys.exit(0)`, which Claude Code interprets as "allow the tool call," meaning any failure in the security boundary silently degrades to "allow" instead of "deny."

### Finding Description
On import, the script tries `from hookify.core.config_loader import load_rules` and `from hookify.core.rule_engine import RuleEngine`. If this import raises `ImportError` (e.g., because `CLAUDE_PLUGIN_ROOT` is unset/wrong, `sys.path` is polluted, a rule file has a syntax bug that some transitive module import depends on, or the plugin directory is partially checked out), the handler prints `{"systemMessage": f"Hookify import error: {e}"}` and calls `sys.exit(0)` [1](#0-0) . Exit code 0 with no `"decision": "block"`/deny payload is treated by Claude Code's hook protocol as "no objection," so the underlying `Bash`/`Edit`/`Write`/`MultiEdit` call proceeds exactly as if no hook were installed and no rules were evaluated.

Separately, inside `main()`, any exception during `json.load(sys.stdin)`, `load_rules(event=event)`, or `engine.evaluate_rules(rules, input_data)` is caught by a blanket `except Exception as e` that again only prints a `systemMessage` and, in the `finally` block, unconditionally calls `sys.exit(0)` — explicitly commented "ALWAYS exit 0 - never block operations due to hook errors" [2](#0-1) .

Because `input_data = json.load(sys.stdin)` is attacker-influenced (the PreToolUse payload contains the tool name and tool input driven by whatever command/edit is being attempted, and rule files under `.claude/hookify.*.local.md` are ordinary repo content that could be malformed or crafted to make `load_rules`/`RuleEngine` throw), an attacker who can influence repository content or the tool invocation (e.g. via a cloned repo containing a broken/crafted hookify rule file, or a Bash/Edit/Write/MultiEdit call whose `tool_input` triggers an edge case in `evaluate_rules`) can deterministically force an exception, and the hook will still allow the operation. There is no separate "fail-closed" fallback path, no distinct exit code for "hook malfunctioned, block by default," and no re-raising or escalation — every code path in this file converges on `sys.exit(0)`.

### Impact Explanation
Hookify's entire purpose is to act as a security/policy boundary that inspects and can block dangerous `Bash`, `Edit`, `Write`, and `MultiEdit` operations before they execute. Because every error path (import failure or runtime exception) results in "allow," any condition that can be steered into throwing an exception — a malformed rule file, an environment where `CLAUDE_PLUGIN_ROOT` resolves incorrectly, or an edge case in `RuleEngine.evaluate_rules` — completely and silently disables the block, letting an otherwise-denied command execute. This matches "Sensitive code, prompt, token, diff, or local file disclosure to an unintended local or remote sink," since a rule set designed to block, e.g., `cat ~/.ssh/id_rsa` or `curl` exfiltration commands, or edits to sensitive files, would be bypassed the moment the hook errors out.

### Likelihood Explanation
The precondition is only that the hook throws an exception during normal operation — this is plausible through fairly mundane paths (a malformed or unusual hookify rule `.md` file checked into a cloned repo, unusual `tool_input` shapes reaching `RuleEngine`, environment/path issues affecting the `hookify` package import). No admin privileges, leaked keys, or social engineering are required; an attacker who can get a victim to open a repository containing a crafted `.claude/hookify.*.local.md` file (a completely normal repo artifact) can trigger this via ordinary Claude Code usage. The bug is deterministic and repeatable given the same malformed input.

### Recommendation
Change the failure semantics so that hook/rule-loading errors fail closed for security-relevant rule categories, or at minimum emit an explicit deny/ask decision (not a bare `systemMessage` with exit 0) when `load_rules`/`RuleEngine` cannot be evaluated. At a minimum, distinguish "no rules configured" (safe to allow) from "rules exist but could not be parsed/evaluated" (should block or prompt the user), and avoid a single blanket `except Exception` + unconditional `sys.exit(0)` for both import and runtime failures.

### Proof of Concept
1. Unit test: create a `.claude/hookify.pretooluse.local.md` rule file with an intentional syntax error (e.g. invalid YAML frontmatter or malformed pattern) that causes `load_rules()` to raise.
2. Configure a rule that should deny `Bash` commands matching `rm -rf /` (or similar sensitive pattern).
3. Invoke `plugins/hookify/hooks/pretooluse.py` via stdin with `{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}`.
4. Assert: with the malformed rule file present, the process exits with code `0` and prints only a `systemMessage`, with no `"decision": "block"` in the output — i.e., the dangerous command is allowed despite a rule that should have blocked it.
5. Additionally simulate an `ImportError` (e.g., by unsetting/corrupting `CLAUDE_PLUGIN_ROOT` or breaking `sys.path`) and confirm the same fail-open behavior at the module-import stage (lines 25-32).

### Citations

**File:** plugins/hookify/hooks/pretooluse.py (L25-32)
```python
try:
    from hookify.core.config_loader import load_rules
    from hookify.core.rule_engine import RuleEngine
except ImportError as e:
    # If imports fail, allow operation and log error
    error_msg = {"systemMessage": f"Hookify import error: {e}"}
    print(json.dumps(error_msg), file=sys.stdout)
    sys.exit(0)
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
