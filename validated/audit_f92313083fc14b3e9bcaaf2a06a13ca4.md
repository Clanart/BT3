### Title
Hookify rule loading is anchored to process CWD instead of the trusted project root, allowing a nested/subagent working directory to substitute or omit root security rules - ([File: plugins/hookify/core/config_loader.py])

### Finding Description
`load_rules()` builds its glob pattern with a relative path: `pattern = os.path.join('.claude', 'hookify.*.local.md')` and resolves it via `glob.glob(pattern)` [1](#0-0) . This pattern is resolved against the Python process's actual working directory at the time the hook script runs — not against `$CLAUDE_PROJECT_DIR`, which the plugin's own documentation identifies as the correct "Project root path" env var available to hook scripts [2](#0-1) . `pretooluse.py`, `posttooluse.py`, and `stop.py` call `load_rules(event=...)` with no directory argument and never `chdir` to or otherwise resolve against a trusted root [3](#0-2) .

The hook input JSON does carry a `cwd` field, but it is never consulted by `config_loader.py` to anchor the glob to the approved project root — the rule set loaded is whatever `.claude/hookify.*.local.md` files exist relative to whatever directory the hook process happens to be started in. Given the changelog's own acknowledgment of prior bugs where worktree-isolated or `cwd:`-overridden subagents ran in or leaked a different working directory than the parent session's checkout [4](#0-3) , and that nested `.claude/` directories are an intended, supported feature where "the agent, workflow, and output-style closest to the working directory now wins when names collide" [5](#0-4) , a workspace/monorepo layout with attacker-influenced nested content is a realistic scenario in which the hookify rule set silently diverges from the intended project-root policy. Because `RuleEngine.evaluate_rules` only aggregates whatever rules were actually loaded — it has no concept of a canonical/expected rule inventory and performs no reconciliation against the project root's rule set — a root `action: block` rule simply never enters `blocking_rules` if it was never loaded (e.g., because CWD pointed elsewhere and its file was never globbed), and any `action: warn` rule found there is emitted as a benign warning instead [6](#0-5) .

### Impact Explanation
If Claude Code (or a plugin-invoked subagent flow) ever executes hooks with the OS process CWD pointing at a nested/attacker-influenced directory rather than the approved project root, the hookify safety net (used to block dangerous Bash commands, dangerous file writes, etc.) is bypassed or weakened without any error or warning to the user: the block rule from the real project is simply absent from the loaded rule list, and the operation proceeds as if no rule existed. This is a workspace confinement / trust-boundary violation: the enforcement policy is bound to a transient, attacker-influenceable filesystem property (CWD) instead of the approved project root.

### Likelihood Explanation
Exploitability depends entirely on whether the hook's OS process CWD can ever differ from the approved project root during a hookify-configured session (e.g., nested checkouts, git worktrees, or a subagent `cwd:` override). The changelog entries referenced show that Claude Code has previously had — and fixed — several bugs where subagent/worktree CWD leaked or diverged from the intended repo root, indicating this is a known class of condition rather than a purely theoretical one. The `config_loader.py` code itself contains no defense (no anchoring to `$CLAUDE_PROJECT_DIR`, no validation that loaded rule files come from the trusted root) — likelihood is limited only by whether such a CWD-divergent invocation is reachable in the currently shipped version, which was not fully confirmed via this repository search alone.

### Recommendation
Anchor `load_rules()` to `os.environ.get('CLAUDE_PROJECT_DIR')` (or the hook's `input_data['cwd']` from the top-level session start, resolved once and validated) instead of the bare relative `.claude` path, and reject/ignore rule files discovered outside that trusted root. Consider also deduplicating rule names across all discovered `.claude` directories so a `warn`-action rule can never silently supersede a `block`-action rule of the same name from a higher-trust scope.

### Proof of Concept
Integration test plan:
1. Create `project_root/.claude/hookify.block-rm.local.md` with `name: block-rm`, `action: block`, `event: bash`, pattern matching `rm -rf`.
2. Create `project_root/nested_dep/.claude/hookify.block-rm.local.md` with the same `name: block-rm` but `action: warn` (or omit the file entirely).
3. Invoke `plugins/hookify/hooks/pretooluse.py` twice with identical stdin JSON (`tool_name: Bash`, `tool_input.command: "rm -rf /"`) but with the OS process CWD set to (a) `project_root` and (b) `project_root/nested_dep`.
4. Assert that in case (a) the hook returns `hookSpecificOutput.permissionDecision: "deny"`.
5. Assert that in case (b) the hook currently returns either an empty `{}` (no rules matched) or only a `systemMessage` warning — demonstrating the block is bypassed purely due to CWD, confirming the vulnerability. After the fix, both invocations should return `permissionDecision: "deny"`.

### Citations

**File:** plugins/hookify/core/config_loader.py (L208-211)
```python

    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
```

**File:** plugins/plugin-dev/skills/hook-development/SKILL.md (L322-329)
```markdown
## Environment Variables

Available in all command hooks:

- `$CLAUDE_PROJECT_DIR` - Project root path
- `$CLAUDE_PLUGIN_ROOT` - Plugin directory (use for portable paths)
- `$CLAUDE_ENV_FILE` - SessionStart only: persist env vars here
- `$CLAUDE_CODE_REMOTE` - Set if running in remote context
```

**File:** plugins/hookify/hooks/pretooluse.py (L51-52)
```python
        # Load rules
        rules = load_rules(event=event)
```

**File:** CHANGELOG.md (L996-996)
```markdown
- Nested `.claude/` directories: the agent, workflow, and output-style closest to the working directory now wins when names collide; project-scope workflow saves now target the closest existing `.claude/workflows/`
```

**File:** CHANGELOG.md (L2596-2596)
```markdown
- Fixed subagents with worktree isolation or `cwd:` override leaking their working directory back to the parent session's Bash tool
```

**File:** plugins/hookify/core/rule_engine.py (L53-94)
```python
        for rule in rules:
            if self._rule_matches(rule, input_data):
                if rule.action == 'block':
                    blocking_rules.append(rule)
                else:
                    warning_rules.append(rule)

        # If any blocking rules matched, block the operation
        if blocking_rules:
            messages = [f"**[{r.name}]**\n{r.message}" for r in blocking_rules]
            combined_message = "\n\n".join(messages)

            # Use appropriate blocking format based on event type
            if hook_event == 'Stop':
                return {
                    "decision": "block",
                    "reason": combined_message,
                    "systemMessage": combined_message
                }
            elif hook_event in ['PreToolUse', 'PostToolUse']:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": hook_event,
                        "permissionDecision": "deny"
                    },
                    "systemMessage": combined_message
                }
            else:
                # For other events, just show message
                return {
                    "systemMessage": combined_message
                }

        # If only warnings, show them but allow operation
        if warning_rules:
            messages = [f"**[{r.name}]**\n{r.message}" for r in warning_rules]
            return {
                "systemMessage": "\n\n".join(messages)
            }

        # No matches - allow operation
        return {}
```
