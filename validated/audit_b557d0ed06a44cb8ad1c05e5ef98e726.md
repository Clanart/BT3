### Title
Symlink-following file write in `setup-ralph-loop.sh` allows workspace escape via `.claude/ralph-loop.local.md` - ([File: plugins/ralph-wiggum/scripts/setup-ralph-loop.sh])

### Summary
`setup-ralph-loop.sh` writes the Ralph loop state file with `cat > .claude/ralph-loop.local.md <<EOF`, which follows symlinks when opening the destination for truncate/write. If an attacker plants a symlink at that path inside a git repository (git supports committing symlinks as `120000` mode blobs pointing to arbitrary absolute paths), cloning the repo and running `/ralph-loop` causes the script to overwrite the symlink target file outside the workspace.

### Finding Description
The script does `mkdir -p .claude` at [1](#0-0)  and then unconditionally writes to the path via shell redirection: [2](#0-1) . Bash's `>` redirection opens the target with `O_CREAT|O_TRUNC|O_WRONLY`, and the kernel resolves symlinks during this open — it does not use `O_NOFOLLOW`. There is no `readlink`, `realpath`, `-L`/`-h` symlink check, or any path-confinement validation anywhere in the script or in the companion `stop-hook.sh` (confirmed by search — no matches for `readlink|realpath|-L|symlink` in the plugin). An attacker who authors a repository (e.g., a public GitHub repo the victim is asked to open in Claude Code) can commit a symlink blob at `.claude/ralph-loop.local.md` pointing to a file outside the repo that is writable by the victim's user, such as `~/.bashrc` or `~/.ssh/authorized_keys`. When the victim clones the repo and later runs the `/ralph-loop` slash command (which invokes this script), the `cat > .claude/ralph-loop.local.md <<EOF` write follows the symlink and truncates/overwrites the out-of-workspace target with the frontmatter/prompt content, some of which (`$PROMPT`) is directly attacker- or user-supplied text passed as the slash-command argument.

### Impact Explanation
This breaks workspace confinement: a plugin script intended to only manage state inside the project's `.claude/` directory can be redirected by a pre-planted symlink to overwrite arbitrary files elsewhere on the victim's filesystem that the victim's user account has write access to (e.g., shell rc files for persistence, or corrupting `~/.ssh/authorized_keys`/other config files). This is an unauthorized file-write/workspace-escape primitive triggered by ordinary repository content plus a normal slash-command invocation, requiring no admin privilege, leaked keys, or social engineering beyond "open this repo and run `/ralph-loop`."

### Likelihood Explanation
Preconditions are minimal and realistic: the victim clones/opens an attacker-controlled repository (a common workflow with Claude Code) and runs `/ralph-loop <prompt>` in that project, which is the plugin's documented normal use. Git faithfully recreates symlinks on checkout on Linux/macOS, so no special tooling is needed by the attacker beyond committing a symlink. The exploit is fully repeatable — the write happens every time the script runs against the pre-existing symlink.

### Recommendation
Before writing, verify `.claude/ralph-loop.local.md` (and the `.claude` directory itself) is not a symlink, e.g. `if [[ -L .claude/ralph-loop.local.md ]]; then echo "refusing to follow symlink" >&2; exit 1; fi`, and/or write to a temp file within `.claude` and atomically `mv` it into place only after confirming the target path resolves inside the project directory (`realpath` comparison against the repo root). Apply the same guard in `stop-hook.sh` before its `mv "$TEMP_FILE" "$RALPH_STATE_FILE"` step at [3](#0-2) .

### Proof of Concept
1. In a fresh git-tracked test repo, run: `mkdir -p .claude && ln -s /tmp/target .claude/ralph-loop.local.md` and create `/tmp/target` with known sentinel content and record its inode.
2. Run `bash plugins/ralph-wiggum/scripts/setup-ralph-loop.sh "test prompt"`.
3. Assert: `/tmp/target` content has been overwritten with the Ralph state frontmatter (i.e., `head -1 /tmp/target` shows `---`), and/or its inode is unchanged but content differs from the sentinel — proving the write followed the symlink outside the workspace (violates WORKSPACE_CONFINEMENT).
4. Expected secure behavior: the script should detect the symlink at `.claude/ralph-loop.local.md`, refuse to write, and exit non-zero, leaving `/tmp/target` content untouched.

### Citations

**File:** plugins/ralph-wiggum/scripts/setup-ralph-loop.sh (L130-131)
```shellscript
# Create state file for stop hook (markdown with YAML frontmatter)
mkdir -p .claude
```

**File:** plugins/ralph-wiggum/scripts/setup-ralph-loop.sh (L140-150)
```shellscript
cat > .claude/ralph-loop.local.md <<EOF
---
active: true
iteration: 1
max_iterations: $MAX_ITERATIONS
completion_promise: $COMPLETION_PROMISE_YAML
started_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
---

$PROMPT
EOF
```

**File:** plugins/ralph-wiggum/hooks/stop-hook.sh (L152-156)
```shellscript
# Update iteration in frontmatter (portable across macOS and Linux)
# Create temp file, then atomically replace
TEMP_FILE="${RALPH_STATE_FILE}.tmp.$$"
sed "s/^iteration: .*/iteration: $NEXT_ITERATION/" "$RALPH_STATE_FILE" > "$TEMP_FILE"
mv "$TEMP_FILE" "$RALPH_STATE_FILE"
```
