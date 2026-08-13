### Title
Unvalidated symlink-follow in `_config_paths`/`_read_config` allows repo-committed `.claude/security-patterns.local.yaml` to read files outside the workspace - (File: plugins/security-guidance/hooks/extensibility.py)

### Summary
`_config_paths` builds project-config candidate paths with plain `os.path.join(cwd, ".claude", basename)` and performs no `os.path.realpath`/containment check against `cwd` before the paths are opened in `_read_config` via a plain `open()`. Because git supports committing symlinks (mode 120000), a malicious repository can ship `.claude/security-patterns.local.yaml` (or the non-local project variant) as a symlink pointing outside the checked-out tree, and `_read_config` will transparently follow it when the victim opens the repo.

### Finding Description
`_config_paths(cwd, basename)` at [1](#0-0)  constructs three candidate paths (`~/.claude/<name>`, `<cwd>/.claude/<name>`, `<cwd>/.claude/<name>.local.<ext>`) purely via string joins, with no `os.path.realpath` call and no check that the resolved path stays within `cwd`. `_load_user_patterns` iterates these candidates and calls `_read_config(candidate)` [2](#0-1) , and `_read_config` opens the file directly with `open(path, ...)` [3](#0-2) , which follows symlinks by default with no realpath verification anywhere in the call chain.

Git can store and check out symlinks as ordinary tracked content (blob mode 120000). An attacker-controlled repository can therefore commit `.claude/security-patterns.local.yaml` (the docstring even calls this file "gitignored" by convention, but nothing enforces that on checkout) as a symlink to an arbitrary path such as `~/.ssh/id_rsa`, `/proc/self/environ`, or another sensitive file on the victim's machine. When the victim opens the repository and the hook runs `load_for_session(cwd)` → `_load_user_patterns(cwd)` → `_config_paths` → `_read_config(candidate)` → `open(path)`, the symlink is silently followed and the target file's bytes are read into the process.

### Impact Explanation
This is a workspace-boundary violation: untrusted repository content causes the hook to read a file chosen by the attacker but located outside the project directory. The blast radius of what becomes visible to the model/user is limited by downstream parsing: `_read_config` requires the content to parse as JSON/YAML, and `_validate_pattern` additionally requires a `patterns` list of dict entries with non-empty `rule_name`/`reminder` keys before any content is turned into an LLM-visible reminder string. For most classic secret files (SSH keys, `/etc/passwd`, `.env` files) this schema will not match, so `_load_user_patterns` will raise (e.g., `AttributeError`/`yaml.YAMLError`) which is caught generically in `load_for_session` and only surfaces via `debug_log` — not directly presented to the model. However, `yaml.YAMLError`/`MarkedYAMLError` string representations typically include a snippet of the offending document text (line/column context), so partial content of the read file could leak into debug logs even on parse failure. The core reachable, provable impact is: an out-of-tree file read triggered purely by opening a malicious repository, with no approval prompt or workspace guard blocking it — a real trust-boundary bypass, though the "full arbitrary content surfaced as reminder text" scenario additionally requires the attacker-chosen target file to happen to be valid YAML matching the plugin's rule schema, which is not something the attacker fully controls (only the path is attacker-controlled, not the target's contents).

### Likelihood Explanation
Feasible and fully attacker-triggerable: it requires only that the victim clones/opens an attacker-authored repository (a routine, expected Claude Code usage flow) with default git symlink support enabled (the default on Linux/macOS, and common on Windows with developer mode / `core.symlinks=true`). No admin privilege, leaked keys, or social engineering beyond "open this repo" is needed. The precondition is realistic and repeatable — a single symlink blob in the repo is sufficient, and the plugin hook loads this file automatically on session start via `load_for_session`.

### Recommendation
In `_config_paths` (or immediately before opening in `_read_config`), resolve each candidate with `os.path.realpath` and verify project-scoped entries ("Project" and "Project (local)") resolve to a path that is still contained within `os.path.realpath(cwd)` (e.g. via `os.path.commonpath` check or `pathlib.Path.is_relative_to`). Reject/skip (with `debug_log`) any candidate whose realpath escapes the project root before calling `open()`. Optionally, also refuse to follow symlinks at all for project-scoped config files (e.g., `os.path.islink(path)` check) since these files are meant to be plain repo content.

### Proof of Concept
Unit/fuzz test plan for `_read_config`/`_load_user_patterns`:
1. Create a temp directory `cwd` and an out-of-tree secret file `/tmp/secret.yaml` containing valid YAML: `patterns: [{rule_name: leak, reminder: "SECRET-CONTENT"}]`.
2. Create `cwd/.claude/security-patterns.local.yaml` as `os.symlink("/tmp/secret.yaml", cwd/.claude/security-patterns.local.yaml)`.
3. Call `_load_user_patterns(cwd)` and assert that no rule with `reminder == "SECRET-CONTENT"` is returned (currently fails: the rule IS returned, proving the escape).
4. Parametrize the symlink target across: absolute out-of-tree path, `../../outside/file.yaml` traversal, and `/proc/self/environ`, asserting in each case that `_config_paths`/`_read_config` either skip the file or that `os.path.realpath(candidate)` is verified to start with `os.path.realpath(cwd)` before any `open()` call.
5. Add an invariant assertion in `_config_paths`: for every returned `(label, path)` where `label != "User"`, `os.path.commonpath([os.path.realpath(path), os.path.realpath(cwd)]) == os.path.realpath(cwd)`.

### Citations

**File:** plugins/security-guidance/hooks/extensibility.py (L92-102)
```python
def _config_paths(cwd: Optional[str], basename: str) -> List[Tuple[str, str]]:
    """Existing config file paths, lowest precedence first (so concat reads in
    precedence order user → project → project-local). Truncation is done on
    the concatenated string, so lowest-precedence content is dropped last."""
    paths = [("User", os.path.expanduser(os.path.join("~", ".claude", basename)))]
    if cwd:
        paths.append(("Project", os.path.join(cwd, ".claude", basename)))
        # claude-security-guidance.local.md / security-patterns.local.yaml
        stem, ext = os.path.splitext(basename)
        paths.append(("Project (local)", os.path.join(cwd, ".claude", f"{stem}.local{ext}")))
    return paths
```

**File:** plugins/security-guidance/hooks/extensibility.py (L147-164)
```python
def _load_user_patterns(cwd: Optional[str]) -> List[Dict[str, Any]]:
    rules: List[Dict[str, Any]] = []
    for label, path in _config_paths(cwd, "security-patterns"):
        # _config_paths returns an extensionless stem (e.g.
        # ".claude/security-patterns" or ".claude/security-patterns.local");
        # try each supported extension.
        for ext in (".yaml", ".yml", ".json"):
            candidate = path + ext
            data = _read_config(candidate)
            if data is None:
                continue
            for entry in (data or {}).get("patterns", []):
                rule = _validate_pattern(entry, source=label)
                if rule:
                    rules.append(rule)
            break  # found one extension; don't double-load .yaml AND .json
        if len(rules) >= PATTERN_MAX_RULES:
            break
```

**File:** plugins/security-guidance/hooks/extensibility.py (L171-177)
```python
def _read_config(path: str) -> Optional[Dict[str, Any]]:
    """Read a YAML or JSON config file. Returns None on missing/malformed."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
```
