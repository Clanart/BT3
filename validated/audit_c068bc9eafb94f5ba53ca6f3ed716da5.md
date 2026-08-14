### Title
Regex-based security patterns in `check_patterns()` are trivially bypassed by newline/whitespace variations, allowing dangerous `subprocess`/`eval` code to land with zero warning - ([File: patterns.py])

### Summary
The `python_subprocess_shell` and `eval_injection` rules in `SECURITY_PATTERNS` use single-line regexes without `re.DOTALL` and without tolerance for whitespace between a call name and its opening parenthesis. An attacker (or Claude itself, unknowingly) can write functionally identical but syntactically reformatted dangerous code — e.g. put `shell=True` on a separate line from `subprocess.run(`, or write `eval (x)` with a space — and `check_patterns()` returns zero matches, so no warning is ever injected via `additionalContext`, even though the code executes exactly as the unsplit form would.

### Finding Description
`check_patterns()` in [1](#0-0)  calls `re.search(pattern["regex"], content)` with no flags. The `python_subprocess_shell` rule regex is: [2](#0-1) 
`r"subprocess\.(?:run|call|Popen|check_output|check_call)\(.*shell\s*=\s*True"` — since `re.DOTALL` is not set, `.` does not match `\n`. Any multi-line call such as:
```python
subprocess.run(
    cmd,
    shell=True,
)
```
places `shell=True` on a line separate from `subprocess.run(`, so `.*` cannot bridge them and the regex fails to match. There is no `substrings` fallback defined for this rule (unlike other rules that combine `substrings` and `regex`), so this construct produces **zero matches** while still being fully executable shell-injection-capable code when Claude later runs it (or when the file is executed as part of the repo).

Similarly, `eval_injection`'s regex is: [3](#0-2) 
`r"(?<![a-zA-Z0-9_\.])eval\("` — this requires `eval` to be immediately followed by `(` with no whitespace. Python syntax permits whitespace between a callable reference and its call parentheses, so `eval (user_input)` is valid, executable code that evaluates arbitrary input identically to `eval(user_input)`, yet fails to match `eval\(` (no `\(` immediately after `eval`), producing zero matches.

`extract_content_from_input` feeds raw `new_string`/`content`/joined `edits` text directly into `check_patterns` [4](#0-3) , so any Edit/Write/MultiEdit tool_input the attacker's crafted repo content leads Claude to produce (e.g. from following attacker-authored code style, snippets, or templates in the repo) can hit this gap. There is no secondary/semantic check (e.g. AST-based) at this layer — the multiline-aware Stop-hook LLM review is a separate, rate-limited, best-effort layer that runs only at end of turn (or is disabled), not a hard gate at edit time. This confirms the pattern-based guard is genuinely bypassable by trivial, natural code formatting, not just adversarial obfuscation.

### Impact Explanation
This is a detection/guardrail bypass, not itself an RCE — but it defeats the entire purpose of the pattern-based rule layer (`ENABLE_PATTERN_RULES`), which is documented as "Fast regex checks that run on every file write... Injects brief warnings" [5](#0-4) . A genuinely dangerous `os.system`/`subprocess(shell=True)`/`eval()` construct can land in the codebase with Claude/the user never being warned, silently defeating a security control the plugin advertises as always-on for every edit. Given the plugin's stated goal (steering Claude toward secure code and warning on dangerous constructs), this is a real, exploitable evasion of the intended guard, with the scoped impact being dangerous code landing unflagged.

### Likelihood Explanation
High feasibility and fully repeatable: no special privileges are needed beyond normal repository content that influences how Claude formats code (e.g. existing code style, linter configs like `black`/`autopep8` that reformat calls across multiple lines, or an attacker directly supplying a file via Edit/Write/MultiEdit tool_input in their own crafted repo). Multi-line function calls and space-before-parenthesis calls are common, natural Python constructs, not exotic corner cases — so this doesn't even require deliberate evasion, only ordinary code formatting.

### Recommendation
- Compile the `python_subprocess_shell` regex with `re.DOTALL` (or restructure to match `subprocess\.(?:run|call|Popen|check_output|check_call)\s*\(` and then separately search for `shell\s*=\s*True` within a bounded balanced-paren span of the call, similar to how `torch_unsafe_load` bounds its lookahead) so it also matches across newlines.
- Add a `substrings`-independent second check, or normalize/collapse whitespace in `content` before matching (e.g. strip newlines within suspected call sites) for these fixed set of highest-severity rules.
- Fix `eval_injection` to allow optional whitespace: `r"(?<![a-zA-Z0-9_\.])eval\s*\("`.
- Consider adding a lightweight AST-based check (Python `ast.parse` + walk for `Call(func=Name(id="eval"))`, `Call(func=Attribute(attr="system"))` with `os` module, and `Call` to `subprocess.*` with a `shell=True` keyword regardless of formatting) as a supplement to the regex layer for `.py` files, since regex over raw text is inherently fragile to formatting variance.

### Proof of Concept
Unit test to add to the plugin's test suite (e.g. `test_patterns.py`):
```python
from patterns import SECURITY_PATTERNS
from security_reminder_hook import check_patterns

def test_subprocess_shell_multiline_bypass():
    content = (
        "import subprocess\n"
        "subprocess.run(\n"
        "    cmd,\n"
        "    shell=True,\n"
        ")\n"
    )
    matches = check_patterns("script.py", content)
    rule_names = {m[0] for m in matches}
    # EXPECTED (desired secure behavior): rule fires
    assert "python_subprocess_shell" in rule_names
    # ACTUAL (current bug): assertion fails — matches is empty because
    # `.` in the regex does not cross the newline before `shell=True`.

def test_eval_space_bypass():
    content = "result = eval (user_input)\n"
    matches = check_patterns("script.py", content)
    rule_names = {m[0] for m in matches}
    # EXPECTED: rule fires since `eval (x)` is valid, executable Python
    assert "eval_injection" in rule_names
    # ACTUAL: fails — regex requires `eval\(` with no space.
```
Fuzz-test plan: generate semantically-equivalent variants of each dangerous call (varying whitespace, line breaks inside argument lists, and keyword-argument ordering) for `os_system_injection`, `python_subprocess_shell`, and `eval_injection`, and assert `check_patterns()` still returns the expected rule name for every variant — currently several variants produce empty results.

### Citations

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L12-15)
```python
1. **Pattern-based rules (PostToolUse, every edit)**: Fast regex checks that run on
   every file write. Detects common vulnerabilities like hardcoded secrets, SQL injection,
   command injection, path traversal, and insecure session configs. Injects brief warnings
   via additionalContext.
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L417-422)
```python
        if not matched and "regex" in pattern and content:
            try:
                if re.search(pattern["regex"], content):
                    matched = True
            except Exception:
                pass
```

**File:** plugins/security-guidance/hooks/security_reminder_hook.py (L429-440)
```python
def extract_content_from_input(tool_name, tool_input):
    """Extract content to check from tool input based on tool type."""
    if tool_name == "Write":
        return tool_input.get("content", "")
    elif tool_name == "Edit":
        return tool_input.get("new_string", "")
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits", [])
        if edits:
            return " ".join(edit.get("new_string", "") for edit in edits)
        return ""
    return ""
```

**File:** plugins/security-guidance/hooks/patterns.py (L100-107)
```python
    {
        "ruleName": "eval_injection",
        # Lookbehind excludes `.` so method calls like PyTorch model.eval(),
        # redis.eval(), spec.eval() don't match. Skip doc/prose files.
        "path_filter": lambda p: not p.endswith(_DOC_EXTS),
        "regex": r"(?<![a-zA-Z0-9_\.])eval\(",
        "reminder": "⚠️ Security Warning: eval() executes arbitrary code and is a major security risk. Use JSON.parse() for data, ast.literal_eval() for Python literals, or a safe expression parser. If this is safe or is explicitly needed, briefly document that in a comment before continuing.",
    },
```

**File:** plugins/security-guidance/hooks/patterns.py (L139-153)
```python
    {
        "ruleName": "python_subprocess_shell",
        "regex": r"subprocess\.(?:run|call|Popen|check_output|check_call)\(.*shell\s*=\s*True",
        "reminder": """⚠️ Security Warning: Using subprocess with shell=True enables command injection.

UNSAFE:
  subprocess.run(f"ls {user_input}", shell=True)
  subprocess.call("grep " + pattern, shell=True)

SAFE - pass arguments as a list without shell:
  subprocess.run(["ls", user_input])
  subprocess.call(["grep", pattern])

When arguments are passed as a list without shell=True, special characters cannot be interpreted as shell metacharacters.""",
    },
```
