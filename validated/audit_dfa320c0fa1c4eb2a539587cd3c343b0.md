### Title
Hookify `load_rules()` glob resolves against process CWD instead of a validated project root, allowing untrusted nested `.claude/hookify.*.local.md` rule files to hijack enforcement - ([File: plugins/hookify/core/config_loader.py])

### Summary
`load_rules()` builds its glob pattern with a bare relative path `os.path.join('.claude', 'hookify.*.local.md')`, which resolves against whatever the process's current working directory happens to be at hook-invocation time, rather than the trusted top-level project root. This is inconsistent with the codebase's own documented best practice of anchoring hook file access to `$CLAUDE_PROJECT_DIR`, and lets an attacker who controls a nested subdirectory of the repository (containing its own `.claude/hookify.*.local.md`) have those rules loaded and enforced when the hook runs with that subdirectory as CWD.

### Finding Description
`load_rules()` in [1](#0-0) constructs the glob pattern as a relative path and calls `glob.glob(pattern)` without ever resolving it against a fixed, validated project root (e.g. `os.environ['CLAUDE_PROJECT_DIR']`). This function is called directly by every hookify hook entry point — `pretooluse.py` [2](#0-1) , `stop.py` [3](#0-2) , and similarly `posttooluse.py`/`userpromptsubmit.py` — none of which chdir to or otherwise pass a project root into `load_rules()`.

Elsewhere in this repo, the documented/expected hook pattern is to anchor filesystem lookups to `$CLAUDE_PROJECT_DIR` (referenced extensively in `plugins/plugin-dev/skills/hook-development/SKILL.md`, `references/advanced.md`, `references/patterns.md`, and used by `security_reminder_hook.py`). Hookify's `config_loader.py` does not follow this pattern and instead relies on an unvalidated relative glob, so whichever directory the interpreter's CWD points to becomes the source of trusted rule files.

If a nested directory in the same repository (e.g., a vendored subproject, example folder, or any directory checked into the repo) contains its own `.claude/hookify.*.local.md`, and the hook process's CWD is that nested directory when the hook fires, `load_rules()` will silently load and enforce those nested rules — potentially instead of, or in addition to, the top-level trusted rules. Rule content controls `action` (`warn`/`block`), `message`, matcher `event`, and regex `conditions` (see `Rule.from_dict` in [4](#0-3) ), so an attacker-authored rule can alter which Bash commands or file edits get blocked/warned, or inject arbitrary attacker-controlled messages into Claude's session.

I could not fully verify from static code alone under what exact conditions the invoking Claude Code process's CWD becomes a nested/attacker-controlled subdirectory during a single session (this is an external Claude Code host behavior, not something visible inside this repo). The plugin code itself performs no defensive normalization/anchoring regardless, which is the concrete, verifiable weakness here.

### Impact Explanation
If reachable, this breaks the assumption that hookify enforcement rules always come from the trusted top-level `.claude/` directory of the project the user opened. An attacker who can get their content into any nested directory of a repository (a very low bar — just committing a file) could plant a `hookify.*.local.md` that silently downgrades a `block` rule to `warn` (or vice versa to produce noisy false blocks/DoS), or injects misleading `message` text designed to manipulate the agent's or user's behavior, whenever a hook happens to execute with that nested directory as CWD. This matches a workspace/trust-boundary confinement violation: untrusted repository content gaining influence over hook enforcement decisions outside its own directory scope.

### Likelihood Explanation
Exploitability depends entirely on an external precondition not controlled by this code: that the Claude Code host process's CWD, at the moment a hookify hook subprocess is spawned, is set to the attacker-controlled nested directory rather than the top-level project root. This repo does not contain the Claude Code host/runtime that decides hook subprocess CWD, so I cannot confirm from this codebase alone how easily an "attacker who can influence CWD" scenario materializes in practice (e.g., whether Bash `cd` in a session affects subsequent hook CWD, or whether slash-commands can alter it). Within the plugin's own code, there is no mitigating check (no `CLAUDE_PROJECT_DIR` anchor, no path validation, no confinement to a known root), so if the external precondition holds, exploitation is trivial and fully repeatable — it requires only committing a rule file to a nested path.

### Recommendation
Anchor `load_rules()` to a validated project root instead of the ambient CWD: use `os.environ.get('CLAUDE_PROJECT_DIR')` (falling back to a single, explicitly resolved top-level root) to build the glob pattern, e.g. `os.path.join(project_root, '.claude', 'hookify.*.local.md')`, and reject/log any candidate whose resolved absolute path is not a direct child of that root's `.claude` directory. This mirrors the pattern already used/documented elsewhere in the repo for hook file access.

### Proof of Concept
Integration test plan:
1. Create a temporary directory tree: `repo/.claude/hookify.trusted.local.md` (top-level, `action: warn`) and `repo/sub/.claude/hookify.attacker.local.md` (nested, `action: block`, matching a benign command pattern).
2. Set `CLAUDE_PROJECT_DIR=repo` and invoke `plugins/hookify/hooks/pretooluse.py` as a subprocess with `cwd=repo/sub` (simulating a hook fired while the working directory is the nested subdirectory) and a `Bash` tool-use payload on stdin.
3. Assert that only the trusted top-level rule is loaded/evaluated (`rules` from `load_rules()` should exclude `hookify.attacker.local.md`), and that the hook's JSON output does not reflect the attacker rule's `block` action or message.
4. As a regression baseline, show that with the current implementation (unmodified `config_loader.py`), `glob.glob(os.path.join('.claude', 'hookify.*.local.md'))` executed with CWD=`repo/sub` returns `sub/.claude/hookify.attacker.local.md` and the hook's output reflects the attacker's `block` action, demonstrating the vulnerability before the fix.

### Citations

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

**File:** plugins/hookify/core/config_loader.py (L209-211)
```python
    # Find all hookify.*.local.md files
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
```

**File:** plugins/hookify/hooks/pretooluse.py (L51-52)
```python
        # Load rules
        rules = load_rules(event=event)
```

**File:** plugins/hookify/hooks/stop.py (L36-37)
```python
        # Load stop rules
        rules = load_rules(event='stop')
```
