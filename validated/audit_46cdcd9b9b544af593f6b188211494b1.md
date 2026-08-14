### Title
AskUserQuestion options for `/hookify:configure` are keyed only by attacker-controllable `name` text with no bound file identifier, allowing rule-selection confusion between colliding rule files - ([File: plugins/hookify/commands/configure.md])

### Finding Description
`configure.md` builds its rule inventory by globbing `.claude/hookify.*.local.md` files and reading each one's `name`/`enabled` frontmatter [1](#0-0) . The `AskUserQuestion` options are then generated purely from that untrusted `name` text and message-derived description, with no file path or other stable identifier attached to the option itself [2](#0-1) . Step 4 instructs the agent to "Determine current state from label" and toggle accordingly, again relying on label text rather than an explicit file-path binding carried from Step 2 [3](#0-2) , and Step 5 edits "the" corresponding file based on that resolution [4](#0-3) .

Because `name` is fully attacker-controlled frontmatter in a rule file [5](#0-4) , an attacker who can contribute a rule file to the project's `.claude/` directory (e.g., via a PR, shared/checked-in `.local.md` file, or a rule created earlier through prompt injection during `/hookify` conversation analysis) can create a second rule whose `name` and `enabled` state are identical or visually confusable to a trusted rule (e.g., both render as `warn-dangerous-rm (currently enabled)`). The workflow has no mechanism visible to the user or specified deterministically in the instructions to disambiguate two options with colliding labels — the only human-facing signal is the label/description text itself, which is attacker-controlled input.

### Impact Explanation
If the malicious rule's message body is a prompt-injection payload designed to instruct Claude to exfiltrate data when triggered, a user attempting to disable it via `/hookify:configure` could instead — due to identical/confusable labels — toggle the benign rule off (leaving the malicious one enabled) or otherwise mis-target the Edit in Step 5. This is a consent-binding failure: the user's selection does not deterministically map to the exact rule/file they believed they were acting on, and the malicious rule survives an intended disable action, causing the exfiltration-message-injecting rule to remain active in the session.

### Likelihood Explanation
Exploitability requires: (1) the attacker being able to place a second `.claude/hookify.*.local.md` file in the project (via a PR/contribution or an earlier injected `/hookify` rule-creation), and (2) that file's `name`/`enabled` state colliding or being confusable with an existing trusted rule's rendered label. This is plausible in shared repos or multi-contributor projects, but requires the specific setup of two coexisting rule files with matching state/name — a narrow but realistic precondition given `.claude/*.local.md` files are documented as ordinarily gitignored but not enforced as such.

### Recommendation
Bind each `AskUserQuestion` option to its source file path explicitly (e.g., include the file path as an internal option value/id, not just the display label), and have Step 4/5 resolve toggles strictly by that bound path rather than by re-parsing the label text. Additionally, detect and surface a warning when two or more rule files share the same `name` value so the user is alerted to the collision before making a selection.

### Proof of Concept
Integration test plan:
1. Create two files: `.claude/hookify.a.local.md` and `.claude/hookify.b.local.md`, both with frontmatter `name: warn-dangerous-rm`, `enabled: true`, but different `pattern`/message bodies (one benign, one containing an exfiltration-instructing message).
2. Run `/hookify:configure` and capture the generated `AskUserQuestion` options — assert both options render as `warn-dangerous-rm (currently enabled)` with no distinguishing file identifier present in the option payload.
3. Simulate user selecting "the option for file a" (disable) and assert the resulting Edit deterministically targets `hookify.a.local.md`'s `enabled` field and never `hookify.b.local.md`.
4. Repeat with reversed file read order (simulate Glob returning files in different order) and assert the mapping from selection to file path remains stable/deterministic — expected failure mode: mapping is ambiguous or order-dependent, confirming the missing invariant.

### Citations

**File:** plugins/hookify/commands/configure.md (L14-32)
```markdown
### 1. Find Existing Rules

Use Glob tool to find all hookify rule files:
```
pattern: ".claude/hookify.*.local.md"
```

If no rules found, inform user:
```
No hookify rules configured yet. Use `/hookify` to create your first rule.
```

### 2. Read Current State

For each rule file:
- Read the file
- Extract `name` and `enabled` fields from frontmatter
- Build list of rules with current state

```

**File:** plugins/hookify/commands/configure.md (L33-66)
```markdown
### 3. Ask User Which Rules to Toggle

Use AskUserQuestion to let user select rules:

```json
{
  "questions": [
    {
      "question": "Which rules would you like to enable or disable?",
      "header": "Configure",
      "multiSelect": true,
      "options": [
        {
          "label": "warn-dangerous-rm (currently enabled)",
          "description": "Warns about rm -rf commands"
        },
        {
          "label": "warn-console-log (currently disabled)",
          "description": "Warns about console.log in code"
        },
        {
          "label": "require-tests (currently enabled)",
          "description": "Requires tests before stopping"
        }
      ]
    }
  ]
}
```

**Option format:**
- Label: `{rule-name} (currently {enabled|disabled})`
- Description: Brief description from rule's message or pattern

```

**File:** plugins/hookify/commands/configure.md (L67-71)
```markdown
### 4. Parse User Selection

For each selected rule:
- Determine current state from label (enabled/disabled)
- Toggle state: enabled → disabled, disabled → enabled
```

**File:** plugins/hookify/commands/configure.md (L73-90)
```markdown
### 5. Update Rule Files

For each rule to toggle:
- Use Read tool to read current content
- Use Edit tool to change `enabled: true` to `enabled: false` (or vice versa)
- Handle both with and without quotes

**Edit pattern for enabling:**
```
old_string: "enabled: false"
new_string: "enabled: true"
```

**Edit pattern for disabling:**
```
old_string: "enabled: true"
new_string: "enabled: false"
```
```

**File:** plugins/hookify/core/config_loader.py (L75-84)
```python
        return cls(
            name=frontmatter.get('name', 'unnamed'),
            enabled=frontmatter.get('enabled', True),
            event=frontmatter.get('event', 'all'),
            pattern=simple_pattern,
            conditions=conditions,
            action=frontmatter.get('action', 'warn'),
            tool_matcher=frontmatter.get('tool_matcher'),
            message=message.strip()
        )
```
