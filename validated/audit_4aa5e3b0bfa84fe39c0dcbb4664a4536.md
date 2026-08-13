### Title
Attacker-triggered exception in Hookify's rule engine causes silent fail-open of `block` rules, bypassing configured tool-call denials - (File: plugins/hookify/hooks/pretooluse.py)

### Summary
The Sherlock finding shows a broad `catch` that was meant to handle one class of failure instead swallows *any* failure (including an attacker-induced one), converting an unexpected error into an unintended state-changing action (auction pause). The Hookify plugin has the same root-cause pattern, but with the opposite (and arguably more dangerous) polarity: any exception raised while evaluating `.claude/hookify.*.local.md` rules is caught by a blanket `except Exception` in the hook entrypoint and converted into an **allow** — silently defeating a user-configured `action: block` rule that was supposed to deny a dangerous `Bash`/`Edit`/`Write` tool call.

### Finding Description
`plugins/hookify/hooks/pretooluse.py` reads the `PreToolUse` hook input, loads rules via `load_rules()`, evaluates them with `RuleEngine.evaluate_rules()`, and prints the resulting permission decision as JSON on stdout, always exiting `0`: [1](#0-0) 

Critically, any exception raised anywhere inside `load_rules()` or `evaluate_rules()` is caught by a top-level `except Exception as e`, which discards the exception and prints only a `systemMessage` — with **no `hookSpecificOutput.permissionDecision: "deny"`** — and then unconditionally `sys.exit(0)`s in `finally`. Since Claude Code's `PreToolUse` hook contract treats "no denial in the JSON output" as an implicit allow, any exception during rule evaluation causes the tool call to proceed exactly as if no rule had matched at all, even if a `block` rule was configured and would have matched.

The rule evaluation path processes attacker/tool-input-controlled data through several code paths that are not fully exception-hardened:
- `RuleEngine._regex_match()` compiles and executes user-defined regex patterns against `command`/`new_text`/`file_path`/`content` fields drawn directly from the tool call being evaluated (i.e., from whatever the agent — potentially steered by adversarial/injected content — is about to execute or write): [2](#0-1) 
- `RuleEngine._extract_field()` builds the text to match against, including reading arbitrary transcript files and concatenating `MultiEdit` edits, `str()`-coercing arbitrary tool_input values: [3](#0-2) 
- `config_loader.extract_frontmatter()` is a hand-rolled, non-standard YAML parser operating on `.claude/hookify.*.local.md` content, with multiple nested nested loops/indices that are not obviously bullet-proof against pathological input: [4](#0-3) 

While `load_rules()`/`load_rule_file()` do catch a broad `Exception` internally and merely skip a bad rule file (not the same "allow" bug), the outer hook script (`pretooluse.py`, and identically `posttooluse.py`, `stop.py`, `userpromptsubmit.py`) wraps the *entire* main body — including `RuleEngine.evaluate_rules()` and, transitively, `_regex_match()`/`_extract_field()` — in one catch-all that fails open on any uncaught exception (e.g. a `re.error` not caught by the inner `except re.error` due to some other exception type such as `RecursionError` from a pathological pattern, a `MemoryError`/`RecursionError` from adversarial regex/text combinations, or a bug triggered by unusual `tool_input` shapes reaching `_extract_field`/`_regex_match`). The 10-second hook `timeout` configured in `hooks.json` compounds this: a slow/catastrophic regex match (ReDoS) against a large `command`/`new_text`/transcript payload can also cause the *hook process itself* to be killed by the external timeout, which similarly results in no denial output and thus an implicit allow — the process-level analog of the gas-limiting attack in the Solidity report: [5](#0-4) 

The structural parallel to the Solidity M-1 bug is direct: a `try`/broad-`catch` construct that is supposed to enforce a security decision (pause the auction / deny the tool call) is instead written so broadly that *any* failure mode — including one an adversary can deliberately induce (OOG via gas-limited call / ReDoS or crafted content via tool input) — routes through the catch branch, and the catch branch's behavior does the opposite of the intended security control (forces a pause vs. silently permits the call).

### Impact Explanation
This falls squarely within the "hook bypass" trust boundary called out in scope: a user who has configured a `block` rule (e.g., to prevent `rm -rf`, edits to `.env`/credentials files, or other dangerous operations) relies on `PreToolUse` to actually deny matching tool calls. If the agent (potentially manipulated via prompt injection from untrusted content it is reading) can cause the rule-evaluation path to throw before it reaches the `blocking_rules` branch, the configured block silently does not apply and the dangerous `Bash`/`Edit`/`Write` call proceeds — with the user given no indication that their safety rule failed to fire (the `systemMessage` only reports "Hookify error: ...", which is easy to miss and does not clearly say "your block rule was NOT enforced"). This is a concrete approval-bypass of a security control the user configured, not merely a cosmetic error-handling nit.

### Likelihood Explanation
Exploitability requires an attacker (or attacker-influenced content the agent is instructed to write/execute) to supply a `command`, `new_text`, `file_path`, or transcript content that reliably raises an exception not covered by the narrower internal `except re.error` in `_regex_match`, or that stalls the hook past its 10s timeout via a ReDoS-style pathological match against an attacker-influenced pattern/text combination. This is analogous to the Solidity finding's requirement that specific preconditions (high founder vesting %) line up — here the precondition is a rule + input combination that trips an uncaught exception or timeout in the evaluation path. Given the rule engine's regex- and string-processing surface is broad and largely untested against adversarial inputs, and given that the fail-open behavior is unconditional and by-design (`# On any error, allow the operation` — an explicit intentional choice, visible verbatim in `pretooluse.py`), this is plausible but conditional, similar in spirit to the accepted Medium-severity classification in the source report.

### Recommendation
- In `pretooluse.py` (and `posttooluse.py`), do not fail open by default when rule evaluation raises. On error, explicitly return `hookSpecificOutput.permissionDecision: "ask"` (or `deny` for high-risk tool types) rather than silently omitting a decision, so a broken/adversarially-triggered rule evaluation cannot be leveraged to defeat a configured `block` rule.
- Harden `_regex_match` with an explicit timeout/complexity guard (e.g., `re.match` with a bounded input length, or a non-backtracking regex engine) so adversarial patterns/inputs cannot cause catastrophic backtracking that either raises or exceeds the hook's 10s timeout.
- Distinguish "no rules matched" from "rule evaluation failed" in the output and surface the latter loudly (e.g., a `systemMessage` that explicitly states a configured block rule could not be evaluated, so the user isn't misled into believing their safety net is active).

### Proof of Concept
Conceptual PoC (exact exception-triggering input was not verified in the index due to inability to execute code in this environment):
1. Create `.claude/hookify.block-rm.local.md` with `action: block`, `event: bash`, `pattern: rm\s+-rf`.
2. Get the agent (e.g., via content it reads/is instructed to process) to attempt a `Bash` tool call whose `command` field is extremely large and/or crafted so that matching against the rule's pattern (or another simultaneously-loaded rule's pattern) triggers catastrophic regex backtracking, exceeding the hook's 10-second timeout in `hooks.json`, or causes `_extract_field`'s `MultiEdit` concatenation / transcript read path to raise an exception not one of `re.error`/`IOError`/`OSError`/`PermissionError`/`UnicodeDecodeError`.
3. Observe: `pretooluse.py`'s outer `except Exception` (or the process-level timeout kill) causes no `permissionDecision: deny` to be emitted; `sys.exit(0)` runs regardless; the dangerous `rm -rf` `Bash` call proceeds despite the configured `block` rule.

I was unable to execute the hooks in a live Claude Code environment to confirm a concrete crashing/timeout-inducing input against `_regex_match`/`_extract_field`; this analysis is based on static code review of the catch-all/fail-open structure, which is the same class of bug as the reported M-1 (broad catch converting an attacker-reachable failure mode into an unintended trust-boundary decision).

### Citations

**File:** plugins/hookify/hooks/pretooluse.py (L35-70)
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

**File:** plugins/hookify/core/config_loader.py (L87-195)
```python
def extract_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """Extract YAML frontmatter and message body from markdown.

    Returns (frontmatter_dict, message_body).

    Supports multi-line dictionary items in lists by preserving indentation.
    """
    if not content.startswith('---'):
        return {}, content

    # Split on --- markers
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content

    frontmatter_text = parts[1]
    message = parts[2].strip()

    # Simple YAML parser that handles indented list items
    frontmatter = {}
    lines = frontmatter_text.split('\n')

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

        # Top-level key (no indentation or minimal)
        if indent == 0 and ':' in line and not line.strip().startswith('-'):
            # Save previous list/dict if any
            if in_list and current_key:
                if in_dict_item and current_dict:
                    current_list.append(current_dict)
                    current_dict = {}
                frontmatter[current_key] = current_list
                in_list = False
                in_dict_item = False
                current_list = []

            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()

            if not value:
                # Empty value - list or nested structure follows
                current_key = key
                in_list = True
                current_list = []
            else:
                # Simple key-value pair
                value = value.strip('"').strip("'")
                if value.lower() == 'true':
                    value = True
                elif value.lower() == 'false':
                    value = False
                frontmatter[key] = value

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

        # Continuation of dict item (indented under list item)
        elif indent > 2 and in_dict_item and ':' in line:
            # This is a field of the current dict item
            k, v = stripped.split(':', 1)
            current_dict[k.strip()] = v.strip().strip('"').strip("'")

    # Save final list/dict if any
    if in_list and current_key:
        if in_dict_item and current_dict:
            current_list.append(current_dict)
        frontmatter[current_key] = current_list

    return frontmatter, message
```

**File:** plugins/hookify/hooks/hooks.json (L1-14)
```json
{
  "description": "Hookify plugin - User-configurable hooks from .local.md files",
  "hooks": {
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
```
