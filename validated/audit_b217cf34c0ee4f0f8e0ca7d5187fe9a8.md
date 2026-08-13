### Title
Frontmatter indentation state machine in `extract_frontmatter` silently drops dict-continuation fields, corrupting authored block-rule conditions - (File: `plugins/hookify/core/config_loader.py`)

### Summary
The hand-rolled YAML-like parser in `extract_frontmatter` uses a strict `indent > 2` check to decide whether a line is a continuation field of a multi-line list-dict item [1](#0-0) . Any continuation line indented with exactly 0–2 spaces matches none of the parser's branches and is silently skipped, so keys such as `operator` or `pattern` never get added to the resulting `Condition` dict, and `Condition.from_dict` fills them with defaults instead of raising an error [2](#0-1) .

### Finding Description
`extract_frontmatter` builds `frontmatter['conditions']` via a small state machine driven by `in_list`/`in_dict_item` flags and raw indentation counts [3](#0-2) . A new list-dict item is opened when a line begins with `-` (regardless of its own indentation) [4](#0-3) , but the *only* branch that appends further `key: value` pairs to that same dict item requires `indent > 2` [5](#0-4) . If a rule author (or attacker-controlled content) writes the list under two-space indentation, e.g.:

```
conditions:
  - field: command
  operator: regex_match
  pattern: "curl.*secrets"
```

the `operator:` and `pattern:` lines have `indent == 2`. They fail the top-level branch (`indent == 0`), fail the list-item branch (`stripped.startswith('-')` is false), and fail the continuation branch (`indent > 2` is false) — so the line matches **no branch** and is dropped without any warning or exception. `load_rule_file`/`load_rules` catches only `ValueError/KeyError/AttributeError/TypeError`/generic exceptions and logs them [6](#0-5) , but this path never raises — it just silently produces a truncated `dict`, so no warning is emitted at all.

The truncated dict is passed to `Condition.from_dict`, which fills missing `operator`/`pattern` with defaults (`'regex_match'` / `''`) [7](#0-6) . This corrupted `Condition` then flows into `RuleEngine._check_condition` / `_regex_match`, where an empty pattern under `regex_match` matches virtually all input (`re.compile('').search(text)` is always truthy) or, for `equals`, almost never matches — silently changing whether the AND-combined rule fires at all [8](#0-7) . Since `_rule_matches` requires *all* conditions to succeed [9](#0-8) , this indentation quirk can flip a hand-authored `block` rule (e.g. one meant to stop secret-exfiltrating `curl` commands) between "matches everything" and "never matches" depending on which operator combination happened to survive parsing — with zero diagnostic output to the user.

Note: the question cites `plugins/plugin-dev/skills/plugin-structure/SKILL.md` as the location, but that file only documents generic plugin layout and does not contain this parser; the actual vulnerable code lives in `plugins/hookify/core/config_loader.py` as shown above.

### Impact Explanation
Hookify's `.claude/hookify.*.local.md` rules are the only local guardrail hooked into `PreToolUse`/`Stop`/etc. to block dangerous actions (e.g. `action: block` rules invoked from `pretooluse.py`) [10](#0-9) . If such a rule file's `conditions` block uses two-space (or otherwise ≤2) indentation for continuation keys, the resulting `Condition` silently loses its intended `pattern`/`operator`, which can make a security-blocking rule fail open (never match) and let a dangerous command (e.g., secret-exfiltrating `curl`) proceed unblocked, or fail closed/over-broad in the opposite direction. This is a trust-boundary/parser-correctness bug in the local enforcement layer that can cause silent security-rule bypass, matching a hook-enforcement-bypass class of impact.

### Likelihood Explanation
The bug fires deterministically for any `conditions` block whose continuation lines use ≤2-space indentation, which is a very plausible/likely YAML-adjacent style choice (e.g., copy-pasted from 2-space YAML conventions) since `extract_frontmatter` is a bespoke parser, not real YAML, and provides no schema validation or error surfacing to catch the mistake. The only precondition is that such a hookify rule file exists and is auto-loaded from `.claude/hookify.*.local.md` (globbed unconditionally by `load_rules`) [11](#0-10) ; this is easily reachable if such a file ships inside a cloned/untrusted repository or plugin content that a victim's Claude Code session picks up.

### Recommendation
Replace the hand-rolled indentation state machine with a real YAML parser (e.g. `yaml.safe_load` on the frontmatter block) so continuation-field indentation is handled per YAML semantics instead of a hardcoded `indent > 2` heuristic. At minimum, track the indentation level established by the first continuation line of each dict item and require all subsequent lines to match it exactly, raising a `ValueError` (rather than silently skipping) whenever a line under an open list/dict scope doesn't match any recognized pattern, so `load_rule_file` will log and reject the malformed rule instead of silently loading a truncated one.

### Proof of Concept
Add a unit test in `plugins/hookify` test suite comparing `extract_frontmatter` output against a reference PyYAML parse for randomized indentation widths:

```python
import yaml
from hookify.core.config_loader import extract_frontmatter, Rule

RULE_TEMPLATE = """---
name: block-exfil-curl
event: bash
action: block
conditions:
{indent}- field: command
{indent}operator: regex_match
{indent}pattern: "curl .* secrets\\.txt"
---
Blocked: secret exfiltration via curl.
"""

def test_condition_parsing_matches_reference_for_all_indents():
    for width in [0, 1, 2, 3, 4]:
        indent = " " * width
        content = RULE_TEMPLATE.format(indent=indent)
        fm, _ = extract_frontmatter(content)
        rule = Rule.from_dict(fm, "")

        # Reference: real YAML always parses the full 3-field dict
        ref_fm = yaml.safe_load(content.split('---')[1])
        expected_cond = ref_fm['conditions'][0]

        assert len(rule.conditions) == 1
        cond = rule.conditions[0]
        assert cond.field == expected_cond['field']
        assert cond.operator == expected_cond['operator'], (
            f"operator dropped/defaulted at indent={width}: got {cond.operator!r}"
        )
        assert cond.pattern == expected_cond['pattern'], (
            f"pattern silently dropped at indent={width}: got {cond.pattern!r} "
            f"expected {expected_cond['pattern']!r}"
        )
```

Expected result: the test fails for `width` in `{0, 1, 2}` because `cond.pattern` becomes `''` and `cond.operator` reverts to the default `'regex_match'` instead of the authored values — demonstrating the silent field-drop and its effect on `RuleEngine._check_condition` semantics (empty-pattern `regex_match` matching everything, or `equals`/other operators effectively disabling the block).

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

**File:** plugins/hookify/core/config_loader.py (L109-122)
```python
    current_key = None
    current_list = []
    current_dict = {}
    in_list = False
    in_dict_item = False

    for line in lines:
        # Skip empty lines and comments
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        # Check indentation level
        indent = len(line) - len(line.lstrip())
```

**File:** plugins/hookify/core/config_loader.py (L154-181)
```python
        # List item (starts with -)
        elif stripped.startswith('-') and in_list:
            # Save previous dict item if any
            if in_dict_item and current_dict:
                current_list.append(current_dict)
                current_dict = {}

            item_text = stripped[1:].strip()

            # Check if this is an inline dict (key: value on same line)
            if ':' in item_text and ',' in item_text:
                # Inline comma-separated dict: "- field: command, operator: regex_match"
                item_dict = {}
                for part in item_text.split(','):
                    if ':' in part:
                        k, v = part.split(':', 1)
                        item_dict[k.strip()] = v.strip().strip('"').strip("'")
                current_list.append(item_dict)
                in_dict_item = False
            elif ':' in item_text:
                # Start of multi-line dict item: "- field: command"
                in_dict_item = True
                k, v = item_text.split(':', 1)
                current_dict = {k.strip(): v.strip().strip('"').strip("'")}
            else:
                # Simple list item
                current_list.append(item_text.strip('"').strip("'"))
                in_dict_item = False
```

**File:** plugins/hookify/core/config_loader.py (L183-187)
```python
        # Continuation of dict item (indented under list item)
        elif indent > 2 and in_dict_item and ':' in line:
            # This is a field of the current dict item
            k, v = stripped.split(':', 1)
            current_dict[k.strip()] = v.strip().strip('"').strip("'")
```

**File:** plugins/hookify/core/config_loader.py (L209-211)
```python
    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
```

**File:** plugins/hookify/core/config_loader.py (L228-239)
```python
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
```

**File:** plugins/hookify/core/rule_engine.py (L120-125)
```python
        # All conditions must match
        for condition in rule.conditions:
            if not self._check_condition(condition, tool_name, tool_input, input_data):
                return False

        return True
```

**File:** plugins/hookify/core/rule_engine.py (L144-181)
```python
    def _check_condition(self, condition: Condition, tool_name: str,
                        tool_input: Dict[str, Any], input_data: Dict[str, Any] = None) -> bool:
        """Check if a single condition matches.

        Args:
            condition: Condition to check
            tool_name: Tool being used
            tool_input: Tool input dict
            input_data: Full hook input data (for Stop events, etc.)

        Returns:
            True if condition matches
        """
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            return False

        # Apply operator
        operator = condition.operator
        pattern = condition.pattern

        if operator == 'regex_match':
            return self._regex_match(pattern, field_value)
        elif operator == 'contains':
            return pattern in field_value
        elif operator == 'equals':
            return pattern == field_value
        elif operator == 'not_contains':
            return pattern not in field_value
        elif operator == 'starts_with':
            return field_value.startswith(pattern)
        elif operator == 'ends_with':
            return field_value.endswith(pattern)
        else:
            # Unknown operator
            return False

```

**File:** plugins/hookify/hooks/pretooluse.py (L51-59)
```python
        # Load rules
        rules = load_rules(event=event)

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)
```
