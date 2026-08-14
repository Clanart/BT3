There is a 10-second `timeout` on each hookify hook invocation, which bounds (though does not eliminate) the impact — this is a meaningful mitigating difference from the on-chain report where `AllocateRewards`/`TerminateEndedRewardsPlans` had no gas-metered ceiling at all. Given this, the analog is a genuine but capped-severity DoS, not an unbounded chain-halt-class hang.

### Title
Unbounded, unmetered iteration over user-supplied `.claude/hookify.*.local.md` rules/conditions on every tool call enables a per-call DoS/hang of Claude Code sessions - ([File: plugins/hookify/core/config_loader.py], [File: plugins/hookify/core/rule_engine.py])

### Summary
The `hookify` plugin globs and parses **all** `.claude/hookify.*.local.md` files on every single `PreToolUse`/`PostToolUse` event and evaluates every rule's every condition (regex match) against tool input/output for every tool call, with no cap on the number of rule files, conditions per rule, or regex complexity — directly analogous to the reported class of "unbounded list iterated in an unmetered per-block/per-call hot path."

### Finding Description
`load_rules()` globs every file matching `.claude/hookify.*.local.md` in the working directory with no limit on file count [1](#0-0) , parses each with a hand-rolled YAML-like frontmatter parser that allows arbitrarily long `conditions:` lists per rule [2](#0-1) , and returns the full rule set with no size ceiling [3](#0-2) .

This unbounded rule set is then re-loaded and fully re-evaluated on **every** `PreToolUse` and `PostToolUse` invocation — i.e., before/after every single tool call in the session [4](#0-3) [5](#0-4) . `evaluate_rules()` loops over every rule and, for each, loops over every condition, compiling/running a regex against the extracted field value [6](#0-5) [7](#0-6) . The regex pattern is fully user/repo-controlled (from frontmatter) and is matched against attacker-influenceable field values including full file contents, transcript file contents, or Bash command strings [8](#0-7) , so a crafted pattern can trigger catastrophic regex backtracking (ReDoS) in addition to sheer O(rules × conditions) cost.

The only bound on this per-tool-call cost is the hook's own 10-second `timeout` configured in `hooks.json` [9](#0-8) , which caps a single invocation's wall-clock impact but does not prevent the same expensive evaluation from re-running (and re-globbing/re-parsing all files from disk) on every subsequent tool call for the rest of the session.

### Impact Explanation
If a victim opens or clones a project (or installs a plugin/skill bundle) containing many `.claude/hookify.*.local.md` files, or a small number of files with very long `conditions:` lists and/or catastrophic-backtracking regex patterns, then every tool call made by the victim's Claude Code session in that project will pay the full, unbounded evaluation cost (or repeatedly hit the 10s hook timeout). This degrades or effectively hangs the interactive session on a per-tool-call basis for as long as the malicious rule files remain in `.claude/`, without any user approval or awareness that such files are driving a hook — a local availability/DoS impact triggered purely by untrusted repository content, requiring only that the `hookify` plugin be enabled.

### Likelihood Explanation
Requires the victim to have the `hookify` plugin enabled and to open a project/worktree containing attacker-supplied `.claude/hookify.*.local.md` files (e.g., a malicious or compromised repository, a shared monorepo, or a submitted PR/branch checked out locally). This is a plausible but non-default-enabled scenario (opt-in plugin), and impact is capped per-invocation by the 10s hook timeout, so it degrades performance/causes repeated hangs rather than a true unbounded halt.

### Recommendation
- Cap the number of `.claude/hookify.*.local.md` files processed and the number of `conditions` per rule in `load_rules()`/`Rule.from_dict()`.
- Enforce a maximum regex pattern length/complexity or use a regex engine with backtracking limits (e.g., `re2`-style) in `_regex_match()`.
- Cap the size of field values passed into regex matching (e.g., truncate `transcript`/file content) in `_extract_field()`.
- Consider caching the parsed rule set instead of re-globbing and re-parsing from disk on every single tool call.

### Proof of Concept
Conceptual (not run against the live tool):
1. In a test project, create `.claude/hookify.0000.local.md` … `.claude/hookify.NNNN.local.md` (thousands of files), each with `event: all` and a `conditions:` list containing a catastrophic-backtracking regex, e.g. `pattern: "(a+)+$"`.
2. Enable the `hookify` plugin and run any Bash/Edit/Write tool call.
3. Observe `pretooluse.py`/`posttooluse.py` re-glob and re-parse all rule files and evaluate every condition against the tool's `command`/`content`/`file_path` on every single subsequent tool call [10](#0-9) , causing each tool call to stall (up to the 10s hook timeout) for the remainder of the session.

### Citations

**File:** plugins/hookify/core/config_loader.py (L50-54)
```python
        # New style: explicit conditions list
        if 'conditions' in frontmatter:
            cond_list = frontmatter['conditions']
            if isinstance(cond_list, list):
                conditions = [Condition.from_dict(c) for c in cond_list]
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

**File:** plugins/hookify/hooks/pretooluse.py (L43-56)
```python
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
```

**File:** plugins/hookify/hooks/posttooluse.py (L37-49)
```python
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
```

**File:** plugins/hookify/core/rule_engine.py (L53-58)
```python
        for rule in rules:
            if self._rule_matches(rule, input_data):
                if rule.action == 'block':
                    blocking_rules.append(rule)
                else:
                    warning_rules.append(rule)
```

**File:** plugins/hookify/core/rule_engine.py (L120-123)
```python
        # All conditions must match
        for condition in rule.conditions:
            if not self._check_condition(condition, tool_name, tool_input, input_data):
                return False
```

**File:** plugins/hookify/core/rule_engine.py (L182-254)
```python
    def _extract_field(self, field: str, tool_name: str,
                      tool_input: Dict[str, Any], input_data: Dict[str, Any] = None) -> Optional[str]:
        """Extract field value from tool input or hook input data.

        Args:
            field: Field name like "command", "new_text", "file_path", "reason", "transcript"
            tool_name: Tool being used (may be empty for Stop events)
            tool_input: Tool input dict
            input_data: Full hook input (for accessing transcript_path, reason, etc.)

        Returns:
            Field value as string, or None if not found
        """
        # Direct tool_input fields
        if field in tool_input:
            value = tool_input[field]
            if isinstance(value, str):
                return value
            return str(value)

        # For Stop events and other non-tool events, check input_data
        if input_data:
            # Stop event specific fields
            if field == 'reason':
                return input_data.get('reason', '')
            elif field == 'transcript':
                # Read transcript file if path provided
                transcript_path = input_data.get('transcript_path')
                if transcript_path:
                    try:
                        with open(transcript_path, 'r') as f:
                            return f.read()
                    except FileNotFoundError:
                        print(f"Warning: Transcript file not found: {transcript_path}", file=sys.stderr)
                        return ''
                    except PermissionError:
                        print(f"Warning: Permission denied reading transcript: {transcript_path}", file=sys.stderr)
                        return ''
                    except (IOError, OSError) as e:
                        print(f"Warning: Error reading transcript {transcript_path}: {e}", file=sys.stderr)
                        return ''
                    except UnicodeDecodeError as e:
                        print(f"Warning: Encoding error in transcript {transcript_path}: {e}", file=sys.stderr)
                        return ''
            elif field == 'user_prompt':
                # For UserPromptSubmit events
                return input_data.get('user_prompt', '')

        # Handle special cases by tool type
        if tool_name == 'Bash':
            if field == 'command':
                return tool_input.get('command', '')

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

        elif tool_name == 'MultiEdit':
            if field == 'file_path':
                return tool_input.get('file_path', '')
            elif field in ['new_text', 'content']:
                # Concatenate all edits
                edits = tool_input.get('edits', [])
                return ' '.join(e.get('new_string', '') for e in edits)

        return None
```

**File:** plugins/hookify/hooks/hooks.json (L4-24)
```json
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse.py",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/posttooluse.py",
            "timeout": 10
          }
        ]
      }
```
