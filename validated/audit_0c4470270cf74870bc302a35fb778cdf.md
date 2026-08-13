### Title
Symlink-following arbitrary file read/exfiltration via `.claude/claude-security-guidance.md` - ([File: plugins/security-guidance/hooks/extensibility.py])

### Summary
`_load_guidance` opens `<cwd>/.claude/claude-security-guidance.md` (and the `.local.md` variant) with a plain `open(path, encoding="utf-8")` call, without checking whether the path is a symlink or verifying the resolved path stays inside the repo/workspace. A malicious repo can commit a symlink at that path pointing to a sensitive file outside the repo (e.g. `~/.ssh/id_rsa`, `~/.aws/credentials`); once the victim opens that repo as `cwd`, the hook reads the target file's bytes and embeds them into the LLM review prompt.

### Finding Description
`_config_paths` (`plugins/security-guidance/hooks/extensibility.py:92-102`) builds candidate paths under `<cwd>/.claude/` for the "Project" and "Project (local)" precedence levels using only `os.path.join`, with no canonicalization or containment check. `_load_guidance` (`plugins/security-guidance/hooks/extensibility.py:105-125`) then does:
```python
with open(path, encoding="utf-8") as f:
    txt = f.read().strip()
```
`open()` follows symlinks by default and there is no `os.path.islink`/`os.path.realpath` + prefix check anywhere in this module (confirmed by searching for `islink`/`realpath`/`readlink` in the file — none present) or in the calling hook. Git supports committing symlinks (mode `120000`); when checked out on a POSIX system, `.claude/claude-security-guidance.md` can be materialized as a real filesystem symlink pointing outside the repo. Once `load_for_session(cwd)` (line 60) invokes `_load_guidance(cwd)`, the target file's content is read, wrapped in `_wrap_guidance` (line 128) into a `<project-security-guidance>` block, exposed via `guidance_block()` (line 79), and folded into the review prompt that `llm.py` sends to the Claude API (`_call_claude`, confirmed present in `llm.py`) — i.e., over the network to a remote endpoint. The module's own trust-model comment (lines 21-26) only addresses *content-based* prompt-injection ("ignore SQL injection") and does not consider that the *file itself* can be a symlink escaping the workspace — this is an out-of-scope gap in the existing threat model, not something the additive-guidance framing mitigates.

### Impact Explanation
This is a workspace-escape / secret-disclosure primitive: arbitrary local files readable by the victim's user account (SSH private keys, cloud credentials, `.netrc`, browser cookie files, etc.) can be exfiltrated to a remote LLM API endpoint simply by the victim opening a booby-trapped repository — no other interaction is required beyond the hook firing during a normal review flow. This matches "secret disclosure" / "workspace confinement bypass" bounty categories, since content that must stay confined to the repo boundary is instead sent off-host.

### Likelihood Explanation
Preconditions are minimal and match ordinary developer workflow: the attacker only needs to get the victim to clone/open a repository containing a symlink at `.claude/claude-security-guidance.md` (or `.local.md`) — e.g., a malicious open-source contribution, a supply-chain repo, or a shared internal repo. No admin privileges, no leaked keys, no social engineering beyond "review this repo" are needed. On POSIX systems where git symlinks are checked out normally (default `core.symlinks=true` on Linux/macOS), this is fully reproducible and repeatable across sessions since `load_for_session` re-reads the file on every hook invocation.

### Recommendation
In `_load_guidance` (and `_read_config`, which has the identical pattern for `security-patterns.*`), before opening the Project/Project-local candidate paths:
- Reject the path if `os.path.islink(path)` is true, or
- Resolve with `os.path.realpath(path)` and verify the result is still under `os.path.realpath(cwd)` (use `os.path.commonpath` or `Path.is_relative_to`), refusing to read and logging via `debug_log` otherwise.
Apply the same guard to both `_load_guidance` and `_read_config` since they share the same `_config_paths`-derived candidates and the same open-follows-symlink issue.

### Proof of Concept
Integration test:
1. Create a temporary directory `repo/` to act as `cwd`, and a sibling file `secret/id_rsa` with known marker content outside `repo/`.
2. Inside `repo/.claude/`, create a symlink `claude-security-guidance.md` -> `../../secret/id_rsa` (or an absolute path to the secret file).
3. Call `extensibility._load_guidance(repo_dir)` (or `load_for_session(repo_dir)` + `guidance_block()`).
4. Assert the marker content from `secret/id_rsa` is NOT present in the returned string, and/or assert the function skips the symlinked path.
5. Currently (pre-fix) this test fails because the secret marker is present in `_load_guidance`'s output, proving the symlink is followed and the file content is disclosed into the string that later reaches `llm._call_claude`.