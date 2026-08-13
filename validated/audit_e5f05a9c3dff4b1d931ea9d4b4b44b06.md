### Title
Legacy `pattern` field in file-event rules never checks `file_path`, silently defeating "block sensitive file" rules created via `/hookify` - (File: `plugins/hookify/core/config_loader.py`)

### Summary
`Rule.from_dict` converts the legacy `pattern` frontmatter field into a `Condition` whose `field` is derived only from `event`, hard-coding `new_text` for all `file` events regardless of what the pattern is meant to match. Both `/hookify`'s own generation instructions and its skill docs tell users/AI to write file-path patterns (e.g. `\.env$`) as a simple `pattern` field for `file` events, but the loader silently checks that pattern against the edited *content* (`new_text`), never the *path* (`file_path`), so any block rule created this way never fires on the condition its author intended.

### Finding Description
`Rule.from_dict` in [1](#0-0)  converts a simple `pattern` value into a single `Condition` whose `field` is chosen purely from `event`: `bash` → `command`, `file` → `new_text`, anything else → `content`. There is no way for the legacy form to target `file_path`.

The explicit/"advanced" form, via `Condition.from_dict` at [2](#0-1) , allows an author to freely set `field: file_path`, which is fully supported by the evaluator's `_extract_field` for `Write`/`Edit` tools at [3](#0-2) .

Both of hookify's own guidance documents instruct users/AI to author *file-path* patterns using the simple legacy `pattern:` field for `file` events:
- `/hookify` command instructions: "**File patterns:** ... Match file paths: `\.env$|\.git/|node_modules/`" under a plain `pattern: {regex pattern}` template at [4](#0-3)  and [5](#0-4) .
- The `writing-rules` skill states the simple `pattern` field "Matches against command (bash) or new_text (file)" at [6](#0-5) , yet the same document lists "Sensitive files: `\.env$`, `credentials`, `\.pem$`" as a "Common pattern" alongside content patterns like `console.log(` for the `file` event at [7](#0-6) .

Consequence: when `/hookify rule creation` (driven by conversation analysis or explicit user instruction, as described in `hookify.md` Steps 1-3) produces a rule such as:
```markdown
---
name: block-sensitive-files
enabled: true
event: file
pattern: \.env$
action: block
---
```
`Rule.from_dict` silently turns this into `Condition(field='new_text', operator='regex_match', pattern='\.env$')` — i.e. it checks whether the *edited content* ends in the literal text `.env` at end-of-line, not whether the *file path* being edited is `.env`. `RuleEngine._rule_matches`/`_check_condition` at [8](#0-7)  then evaluates against the wrong field, so the intended block on editing `.env`/credential files essentially never triggers for realistic edit content, while the equivalent explicit `conditions: [{field: file_path, operator: regex_match, pattern: '\.env$'}]` form (also documented and used by hookify's own examples, e.g. [9](#0-8) ) works correctly. This is exactly the legacy vs. explicit differential: identical stated intent, different — and in the legacy case, ineffective — actual enforcement.

No validation exists anywhere in `load_rule_file`/`load_rules` ( [10](#0-9) ) or `RuleEngine.evaluate_rules` ( [11](#0-10) ) to detect that a legacy `pattern` rule for `file` events cannot express a `file_path` check, or to warn that a "block .env edits" style rule is a no-op.

### Impact Explanation
A rule author (human user or the `/hookify` AI-driven generation flow, following the plugin's own documented guidance) can end up with a `.claude/hookify.*.local.md` rule with `action: block` that appears to block edits/writes to sensitive files (`.env`, `credentials`, `.pem`, etc.) but never actually enforces that block, because the legacy pattern is checked against edit content instead of the file path. Any subsequent Edit/Write/MultiEdit operation to such a file — including one driven by untrusted content encountered during a session (e.g. prompt injection instructing the agent to modify `.env`) — proceeds without being blocked, resulting in unauthorized file write outside the intended protection scope. This matches the "Unauthorized file read or write outside the user-approved workspace or target scope" impact category.

### Likelihood Explanation
This requires no special privilege: any use of `/hookify` to create a sensitive-file "block" rule via the simple `pattern` form (which the command's and skill's own docs explicitly recommend for file-path patterns) reproduces the bug deterministically. It is 100% reliable and repeatable — it is a structural difference in `Rule.from_dict`'s field-inference logic, not a probabilistic condition. Any repo-shipped rule file using the same legacy pattern convention is equally affected.

### Recommendation
- In `Rule.from_dict`, do not silently infer `field='new_text'` for all `file`-event legacy patterns. Either: (a) require authors to specify `field` explicitly whenever `event == 'file'` and reject/warn on ambiguous legacy `pattern` rules, or (b) generate two conditions (OR'd) checking both `file_path` and `new_text` when using the legacy form for `file` events, matching what most authors intend from the documented "match file paths" guidance.
- Update `plugins/hookify/commands/hookify.md` and `plugins/hookify/skills/writing-rules/SKILL.md` to stop suggesting `\.env$`-style file-path patterns as simple `pattern:` values for `file` events, since that field is only ever compared to `new_text`.
- Add a lint/self-check step in rule loading that flags "block" rules whose only field is `new_text` but whose pattern looks like a path/extension pattern (e.g., contains `$` anchors typical of filenames), or require `field: file_path` explicitly for such intents.

### Proof of Concept
Unit test in `plugins/hookify/core/config_loader.py` / `rule_engine.py` style:
```python
from hookify.core.config_loader import extract_frontmatter, Rule
from hookify.core.rule_engine import RuleEngine

# Legacy form the docs recommend for blocking .env edits
legacy_md = """---
name: block-sensitive-files
enabled: true
event: file
pattern: \\.env$
action: block
---

Blocked: editing .env files is not allowed.
"""
fm, msg = extract_frontmatter(legacy_md)
legacy_rule = Rule.from_dict(fm, msg)
assert legacy_rule.conditions[0].field == 'new_text'  # BUG: should be file_path

engine = RuleEngine()
input_data = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Write",
    "tool_input": {"file_path": ".env", "content": "SECRET=abc123"}
}
result = engine.evaluate_rules([legacy_rule], input_data)
assert result == {}  # FAILS the intended security policy: write to .env is NOT blocked

# Explicit form correctly blocks the same write
explicit_md = """---
name: block-sensitive-files-explicit
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \\.env$
---

Blocked: editing .env files is not allowed.
"""
fm2, msg2 = extract_frontmatter(explicit_md)
explicit_rule = Rule.from_dict(fm2, msg2)
result2 = engine.evaluate_rules([explicit_rule], input_data)
assert result2.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
```
Expected assertions demonstrate that the legacy form and explicit form produce different (non-equivalent) blocking outcomes for the identical stated intent, violating the invariant that legacy and explicit rule forms must produce the same effective security semantics, and allowing unauthorized writes to `.env` under a rule that was supposed to block them.

### Citations

**File:** plugins/hookify/core/config_loader.py (L22-29)
```python
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Condition':
        """Create Condition from dict."""
        return cls(
            field=data.get('field', ''),
            operator=data.get('operator', 'regex_match'),
            pattern=data.get('pattern', '')
        )
```

**File:** plugins/hookify/core/config_loader.py (L56-73)
```python
        # Legacy style: simple pattern field
        simple_pattern = frontmatter.get('pattern')
        if simple_pattern and not conditions:
            # Convert simple pattern to condition
            # Infer field from event
            event = frontmatter.get('event', 'all')
            if event == 'bash':
                field = 'command'
            elif event == 'file':
                field = 'new_text'
            else:
                field = 'content'

            conditions = [Condition(
                field=field,
                operator='regex_match',
                pattern=simple_pattern
            )]
```

**File:** plugins/hookify/core/config_loader.py (L198-274)
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

**File:** plugins/hookify/core/rule_engine.py (L35-94)
```python
    def evaluate_rules(self, rules: List[Rule], input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate all rules and return combined results.

        Checks all rules and accumulates matches. Blocking rules take priority
        over warning rules. All matching rule messages are combined.

        Args:
            rules: List of Rule objects to evaluate
            input_data: Hook input JSON (tool_name, tool_input, etc.)

        Returns:
            Response dict with systemMessage, hookSpecificOutput, etc.
            Empty dict {} if no rules match.
        """
        hook_event = input_data.get('hook_event_name', '')
        blocking_rules = []
        warning_rules = []

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

**File:** plugins/hookify/core/rule_engine.py (L96-125)
```python
    def _rule_matches(self, rule: Rule, input_data: Dict[str, Any]) -> bool:
        """Check if rule matches input data.

        Args:
            rule: Rule to evaluate
            input_data: Hook input data

        Returns:
            True if rule matches, False otherwise
        """
        # Extract tool information
        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input', {})

        # Check tool matcher if specified
        if rule.tool_matcher:
            if not self._matches_tool(rule.tool_matcher, tool_name):
                return False

        # If no conditions, don't match
        # (Rules must have at least one condition to be valid)
        if not rule.conditions:
            return False

        # All conditions must match
        for condition in rule.conditions:
            if not self._check_condition(condition, tool_name, tool_input, input_data):
                return False

        return True
```

**File:** plugins/hookify/core/rule_engine.py (L235-244)
```python
        elif tool_name in ['Write', 'Edit']:
            if field == 'content':
                # Write uses 'content', Edit has 'new_string'
                return tool_input.get('content') or tool_input.get('new_string', '')
            elif field == 'new_text' or field == 'new_string':
                return tool_input.get('new_string', '')
            elif field == 'old_text' or field == 'old_string':
                return tool_input.get('old_string', '')
            elif field == 'file_path':
                return tool_input.get('file_path', '')
```

**File:** plugins/hookify/commands/hookify.md (L97-98)
```markdown
pattern: {regex pattern}
action: {warn|block}
```

**File:** plugins/hookify/commands/hookify.md (L108-124)
```markdown
**For more complex rules (multiple conditions):**
```markdown
---
name: {rule-name}
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.env$
  - field: new_text
    operator: contains
    pattern: API_KEY
---

{Warning message}
```
```

**File:** plugins/hookify/commands/hookify.md (L172-174)
```markdown
**File patterns:**
- Match code patterns: `console\.log\(|eval\(|innerHTML\s*=`
- Match file paths: `\.env$|\.git/|node_modules/`
```

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L53-56)
```markdown
**pattern** (simple format): Regex pattern to match
- Used for simple single-condition rules
- Matches against command (bash) or new_text (file)
- Python regex syntax
```

**File:** plugins/hookify/skills/writing-rules/SKILL.md (L176-180)
```markdown
**Common patterns:**
- Debug code: `console\.log\(`, `debugger`, `print\(`
- Security risks: `eval\(`, `innerHTML\s*=`, `dangerouslySetInnerHTML`
- Sensitive files: `\.env$`, `credentials`, `\.pem$`
- Generated files: `node_modules/`, `dist/`, `build/`
```
