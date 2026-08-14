### Title
Stop-hook completion gating relies on spoofable "reason"/"transcript" free text instead of verified tool-call history - (File: plugins/hookify/core/rule_engine.py)

### Summary
The `hookify` Stop hook's `RuleEngine._extract_field`/`_check_condition` evaluates `stop` rule conditions against the `reason` field (agent-supplied free text) or the `transcript` file content, and `evaluate_rules` uses only this text-based match to decide `decision: block` vs. allow. Because these fields are natural-language content that the agent itself produces (and which can be influenced by untrusted repo/PR/issue content the agent has read), an attacker who can shape what ends up in the agent's stated "reason" or transcript can cause a "tests must run" completion-gate rule to falsely match or fail to match, without any verification that tests were actually executed via a tool call.

### Finding Description
`stop.py:main()` reads hook input, calls `RuleEngine.evaluate_rules(rules, input_data)`, and for `hook_event == 'Stop'` returns `{"decision": "block", "reason": ..., "systemMessage": ...}` purely based on `_rule_matches` results (`plugins/hookify/core/rule_engine.py:60-71`). `_rule_matches` iterates `rule.conditions` and calls `_check_condition`, which for Stop events extracts values via `_extract_field` from `field == 'reason'` (`input_data.get('reason', '')`) or `field == 'transcript'` (reads the transcript file at `transcript_path`) — both are free-text content, not verified tool invocation records (`plugins/hookify/core/rule_engine.py:205-225`). The documented pattern for enforcing "tests must run" is exactly this style of Stop rule matching text content (`plugins/hookify/skills/writing-rules/SKILL.md:182-201`, `README.md`), with no field or mechanism referencing actual `PostToolUse`/tool-call history (e.g., a recorded Bash test invocation) to corroborate the claim. Since `contains`/`regex_match`/`equals` operate on unstructured text that originates from or is influenced by the agent's summary of its own actions (itself potentially influenced by attacker-controlled PR/issue/file content it ingested), an attacker-crafted phrase embedded in ingested content can cause the agent to include (or omit) trigger phrases in its stop `reason`, flipping the block decision independent of whether tests were actually run. There is no session-bound, tool-call-derived state (e.g., a ledger of executed Bash commands from `PostToolUse` hooks) that the Stop-time evaluation cross-checks; the gate is purely a substring/regex test of narrative text.

### Impact Explanation
This allows the agent to stop and report completion while skipping an enforced quality gate (e.g., "tests must run before stopping") when the underlying trigger text can be manipulated, undermining the plugin's intended process-enforcement/completion-gating guarantee. Impact is a policy/enforcement bypass within the hookify plugin's automation-gating feature — not memory corruption or key leakage, but a violation of the intended invariant that completion checks reflect actual verified actions. Scoped impact: false negative/positive completion gating for hookify Stop rules, which is the mechanism repos rely on to prevent untested/unreviewed changes from being finalized by the agent.

### Likelihood Explanation
Feasibility is moderate to high in any project using hookify Stop rules for enforcement: the attacker needs no special privileges beyond the ability to place content that the agent will read (PR/issue text, files) or otherwise influence agent-generated `reason` text/transcript content, which is an ordinary, always-reachable interaction surface for an agentic coding tool. No additional bypass of allowlists/sandboxing is required because the enforcement point itself is the text field, so any crafted phrase satisfying/evading the configured `contains`/`regex_match` pattern reproducibly changes the block decision, deterministically and repeatably.

### Recommendation
Bind Stop-event completion-gating decisions to verifiable, tamper-resistant state rather than to `reason`/`transcript` free text: maintain a session-local record of tool invocations (e.g., persisted from `PostToolUse` events, keyed by session ID) and have Stop rules for "must run tests"-style checks query that structured, hook-observed history (e.g., "was a Bash command matching `pytest|npm test` executed and did it exit 0 since last relevant file edit") instead of, or in addition to, matching narrative text. If narrative-text matching remains supported for advisory/warning rules, clearly document that `reason`/`transcript`-based Stop conditions are advisory only and must not be relied on for hard `action: block` enforcement of "tests were run" invariants.

### Proof of Concept
Integration test plan for `plugins/hookify/core/rule_engine.py::RuleEngine.evaluate_rules`:
1. Define a Stop rule equivalent to a "require tests" block rule:
   ```python
   rule = Rule(name="require-tests", enabled=True, event="stop", action="block",
               conditions=[Condition(field="reason", operator="not_contains", pattern="tests passed")],
               message="Tests must run before stopping")
   ```
2. Simulate an attacker-influenced agent that, having ingested crafted PR/issue text instructing it to phrase its completion reason containing "tests passed", stops without ever invoking a Bash test command:
   ```python
   input_data = {"hook_event_name": "Stop", "reason": "All changes complete, tests passed.", "transcript_path": "/tmp/fake_transcript.txt"}
   result = engine.evaluate_rules([rule], input_data)
   assert result == {}  # demonstrates bypass: no block despite tests never actually running via a tool call
   ```
3. Assert that no corroborating tool-call record exists (no `Bash` `PostToolUse` entry running a test command) yet the rule engine still returns an empty/allow result — proving the decision is bound to spoofable text rather than actual tool-use history.
4. Contrast with the expected fix: engine should consult a structured tool-history store and only allow when it finds an actual passing test invocation record; test should assert `decision == "block"` when that structured evidence is absent, regardless of `reason` content.