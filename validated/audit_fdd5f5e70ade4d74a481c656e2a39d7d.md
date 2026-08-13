### Title
Hookify's `load_rules()` resolves rule files relative to process `cwd` instead of `CLAUDE_PROJECT_DIR`, allowing block rules to be silently skipped when cwd drifts from the project root - (File: plugins/hookify/core/config_loader.py)

### Summary
`load_rules()` finds hookify rule files with a `cwd`-relative glob (`os.path.join('.claude', 'hookify.*.local.md')`), and none of the four hook entry points (`pretooluse.py`, `posttooluse.py`, `stop.py`, `userpromptsubmit.py`) resolve or `chdir` to `CLAUDE_PROJECT_DIR` before calling it. If the hook process's working directory is anywhere other than the top-level project root that has `.claude/hookify.*.local.md` files, `load_rules()` silently returns an empty (or wrong-project) rule set, so configured `block` rules do not fire for tool calls issued from that directory.

### Finding Description
`load_rules()` builds its glob pattern purely from the relative string `.claude/hookify.*.local.md` and calls `glob.glob(pattern)` with no anchoring to any project-root variable: [1](#0-0) 

None of the hook drivers change directory or otherwise pin the search location; they only manipulate `sys.path` from `CLAUDE_PLUGIN_ROOT` (the plugin's own install location, unrelated to the project directory) and then call `load_rules()` directly: [2](#0-1) 

The same pattern repeats in `posttooluse.py`, `stop.py`, and `userpromptsubmit.py`. [3](#0-2) 

By contrast, Claude Code's own plugin-development guidance documents `CLAUDE_PROJECT_DIR` as the environment variable hooks should use to reliably reference the project root regardless of current working directory, which hookify never references anywhere in its code: [4](#0-3) 
A repo-wide search confirms `CLAUDE_PROJECT_DIR` is used by `plugin-dev`'s documentation/scripts and `security-guidance`'s hook, but not once inside `plugins/hookify/`.

Attack/trigger flow: repository content (e.g., a nested submodule, README instructions, or any prompt-injectable file) that causes Claude to operate with `cwd` pointed at a subdirectory lacking its own `.claude/` folder — while `CLAUDE_PROJECT_DIR` still correctly points at the real root — results in `load_rules()` returning `[]`. Any `block`-action hookify rule configured at the top-level project (e.g., "block `rm -rf`", "block edits to `.env`") is then not evaluated at all for tool calls made in that scoped context, and `RuleEngine.evaluate_rules([], ...)` returns `{}` (no deny decision), so the PreToolUse hook allows the operation.

### Impact Explanation
This is a hook-enforcement/workspace-scoping bypass: security rules the user explicitly configured to `block` dangerous operations (destructive `rm -rf`, edits to secret files, etc.) become no-ops purely because of a directory-resolution bug, not because the user disabled them. This matches an "unauthorized command/file action" / "trust-boundary bypass" impact class — a user relying on hookify block rules for safety guardrails would have those guardrails silently disabled for tool calls scoped to a subdirectory, without any warning.

### Likelihood Explanation
The bug is deterministic and requires no privilege beyond ordinary repository content: any scenario where Claude Code's working directory for a tool invocation differs from the top-level project root (nested clones/monorepo subprojects, `cd` performed by an earlier Bash step, subagent scoped to a package directory) reproduces it every time, since `load_rules()` has no fallback to `CLAUDE_PROJECT_DIR` and silently swallows the "no files found" case as an empty rule list rather than erroring.

### Recommendation
Anchor `load_rules()`'s glob to `os.environ.get('CLAUDE_PROJECT_DIR')` (falling back to `cwd` only if the variable is unset), i.e. build the pattern as `os.path.join(os.environ.get('CLAUDE_PROJECT_DIR', os.getcwd()), '.claude', 'hookify.*.local.md')`, matching the pattern already documented/used elsewhere in the codebase (e.g. `security-guidance/hooks/security_reminder_hook.py`, `plugin-dev` hook scripts).

### Proof of Concept
Integration test plan:
1. Create a temp project root `root/` containing `root/.claude/hookify.block-rm.local.md` with `action: block`, `event: bash`, `pattern: rm\s+-rf`.
2. Create `root/sub/` with no `.claude/` directory.
3. Set env `CLAUDE_PROJECT_DIR=<abs path to root>`, and invoke `plugins/hookify/hooks/pretooluse.py` as a subprocess with `cwd=root/sub` and stdin JSON `{"tool_name":"Bash","tool_input":{"command":"rm -rf /tmp/x"}}`.
4. Current behavior: `load_rules()` (called with no cwd/env fix) returns `[]`, `evaluate_rules` returns `{}`, hook prints `{}` — assert this currently happens (demonstrating the bug).
5. After the fix, assert the hook output contains `"permissionDecision": "deny"` and the rule's message, proving rules configured at `CLAUDE_PROJECT_DIR` are discovered and enforced regardless of `cwd`.

### Citations

**File:** plugins/hookify/core/config_loader.py (L198-212)
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

```

**File:** plugins/hookify/hooks/pretooluse.py (L12-52)
```python
# CRITICAL: Add plugin root to Python path for imports
# We need to add the parent of the plugin directory so Python can find "hookify" package
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    # Add the parent directory of the plugin
    parent_dir = os.path.dirname(PLUGIN_ROOT)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    # Also add PLUGIN_ROOT itself in case we have other scripts
    if PLUGIN_ROOT not in sys.path:
        sys.path.insert(0, PLUGIN_ROOT)

try:
    from hookify.core.config_loader import load_rules
    from hookify.core.rule_engine import RuleEngine
except ImportError as e:
    # If imports fail, allow operation and log error
    error_msg = {"systemMessage": f"Hookify import error: {e}"}
    print(json.dumps(error_msg), file=sys.stdout)
    sys.exit(0)


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
```

**File:** plugins/hookify/hooks/posttooluse.py (L12-45)
```python
# CRITICAL: Add plugin root to Python path for imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    parent_dir = os.path.dirname(PLUGIN_ROOT)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    if PLUGIN_ROOT not in sys.path:
        sys.path.insert(0, PLUGIN_ROOT)

try:
    from hookify.core.config_loader import load_rules
    from hookify.core.rule_engine import RuleEngine
except ImportError as e:
    error_msg = {"systemMessage": f"Hookify import error: {e}"}
    print(json.dumps(error_msg), file=sys.stdout)
    sys.exit(0)


def main():
    """Main entry point for PostToolUse hook."""
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Determine event type based on tool
        tool_name = input_data.get('tool_name', '')
        event = None
        if tool_name == 'Bash':
            event = 'bash'
        elif tool_name in ['Edit', 'Write', 'MultiEdit']:
            event = 'file'

        # Load rules
        rules = load_rules(event=event)
```

**File:** plugins/plugin-dev/skills/hook-development/SKILL.md (L1-18)
```markdown
---
name: Hook Development
description: This skill should be used when the user asks to "create a hook", "add a PreToolUse/PostToolUse/Stop hook", "validate tool use", "implement prompt-based hooks", "use ${CLAUDE_PLUGIN_ROOT}", "set up event-driven automation", "block dangerous commands", or mentions hook events (PreToolUse, PostToolUse, Stop, SubagentStop, SessionStart, SessionEnd, UserPromptSubmit, PreCompact, Notification). Provides comprehensive guidance for creating and implementing Claude Code plugin hooks with focus on advanced prompt-based hooks API.
version: 0.1.0
---

# Hook Development for Claude Code Plugins

## Overview

Hooks are event-driven automation scripts that execute in response to Claude Code events. Use hooks to validate operations, enforce policies, add context, and integrate external tools into workflows.

**Key capabilities:**
- Validate tool calls before execution (PreToolUse)
- React to tool results (PostToolUse)
- Enforce completion standards (Stop, SubagentStop)
- Load project context (SessionStart)
- Automate workflows across the development lifecycle
```
