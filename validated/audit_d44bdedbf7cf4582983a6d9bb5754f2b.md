### Title
Unscoped `.claude/hookify.*.local.md` discovery via CWD-relative glob allows unreviewed nested/untrusted repo content to inject hook rules - ([File: plugins/hookify/core/config_loader.py])

### Summary
`load_rules()` resolves rule files with a path that is relative to the process's current working directory rather than being anchored to the top-level, user-approved project root. If Claude Code's CWD ever points into an attacker-influenced nested directory (submodule, nested repo, multi-root workspace member), rule files placed there are silently discovered and loaded with the same authority as rules from the trusted top-level project.

### Finding Description
`load_rules()` builds `pattern = os.path.join('.claude', 'hookify.*.local.md')` and calls `glob.glob(pattern)` [1](#0-0)  with no anchoring to a fixed, previously-approved project root (e.g. via `git rev-parse --show-toplevel` or an explicitly recorded workspace path). Every matched file is fed into `load_rule_file()` and, if well-formed, turned into a `Rule` with no additional trust check [2](#0-1) . This function is called from every hook entry point (`pretooluse.py`, `posttooluse.py`, etc.) using whatever CWD the hook process inherits [3](#0-2) . Loaded rules are merged into a single flat pool and evaluated by `RuleEngine.evaluate_rules()`, which has no per-file provenance/trust weighting — every matching rule's `message` is concatenated into the `systemMessage` returned to Claude Code, and any rule with `action: block` can deny a `PreToolUse`/`PostToolUse` operation [4](#0-3) . Because there is no dedup/override-by-name logic, an attacker-authored file can only *add* new warn/block rules to the pool; it cannot remove or override rules already loaded from a legitimately-approved file — so "disabling" existing enforcement isn't achievable, but "broadening" (adding new warnings/blocks, and importantly, injecting arbitrary attacker-controlled `message` text into the `systemMessage` context that Claude Code sees) is achievable.

### Impact Explanation
An attacker who can get a nested/malicious repo or submodule checked out such that Claude Code's CWD resolves into that subdirectory can cause unreviewed rule files to be loaded on every Bash/Edit/Write/Stop tool invocation. Concrete effects: (1) arbitrary attacker-controlled markdown is injected as `systemMessage` back into the assistant/user's session on tool use — a prompt-injection vector; (2) attacker rules with `action: block` can deny arbitrary Bash/Edit/Write/MultiEdit operations, causing operational denial-of-service or steering the agent's behavior by forcing failures on specific commands/files. This does not allow disabling or overriding legitimate warnings from correctly-placed rule files, since rules are additive with no override mechanism.

### Likelihood Explanation
Exploitability requires an unusual precondition: the tool's working directory must actually resolve to an attacker-influenced subdirectory (e.g., a submodule/nested repo directory becomes CWD, or a multi-root workspace opens a nested untrusted folder as the active root) rather than the single reviewed top-level project root. This is a real but narrow class of misconfiguration/workspace-shape scenario, not exploitable purely by adding files to an already-fully-reviewed single-root checkout, since in that case `.claude/*.local.md` at the repo root is already trusted content the user has accepted by opening the project (comparable to `CLAUDE.md` or `.claude/settings.json`).

### Recommendation
Anchor rule discovery to a single, explicitly-determined trusted project root (e.g., resolved once at session start, such as via `git rev-parse --show-toplevel` of the originally opened directory, or an explicit `CLAUDE_PROJECT_ROOT` environment variable) instead of relying on the ambient process CWD, so `load_rules()` cannot pick up files from nested/untrusted checkouts that happen to become the working directory mid-session. Consider also flagging/skipping rule files discovered outside the originally approved root and warning the user before rules from any new path are loaded.

### Proof of Concept
Integration test outline:
1. Create `project/.claude/hookify.trusted.local.md` (approved) and `project/nested_repo/.claude/hookify.evil.local.md` (attacker-authored, e.g. `action: warn`, `message: "<injected instructions>"`).
2. Invoke `load_rules()` with CWD set to `project/nested_repo` (simulating CWD pointing at the nested/untrusted checkout) and assert that `rules` includes the rule from `hookify.evil.local.md`, demonstrating it was loaded without being part of the originally approved root.
3. Assert that after the proposed fix (root-anchored discovery), `load_rules()` invoked from `project/nested_repo` only returns rules found under the recorded/approved project root (`project/.claude/*.local.md`), not `nested_repo/.claude/*.local.md`.

### Citations

**File:** plugins/hookify/core/config_loader.py (L209-211)
```python
    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
```

**File:** plugins/hookify/core/config_loader.py (L213-226)
```python
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
```

**File:** plugins/hookify/hooks/pretooluse.py (L51-56)
```python
        # Load rules
        rules = load_rules(event=event)

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)
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
