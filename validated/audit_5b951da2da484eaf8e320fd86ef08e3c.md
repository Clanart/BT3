No symlink checks, `realpath` containment, or canonicalization exist around this file read — confirming there is no guard against symlink-based path escape in this code path.

### Title
Symlink in a cloned/merged repository causes `getResolutionDiff` to read files outside the repository - ([File: app/src/lib/git/diff.ts])

### Summary
`getResolutionDiff` builds an absolute path by joining the trusted `repository.path` with an attacker-influenced relative `filePath` and reads it directly with Node's `readFile`, which follows symlinks. Because nothing in the merge-conflict / status pipeline rejects symlinked working-tree entries before this read, a git repository that ships a symlinked file at a conflicted path can make Desktop read and render the contents of an arbitrary file on disk outside the repository when the user opens the new Copilot-assisted conflict resolution view.

### Finding Description
`getResolutionDiff` is invoked with `filePath` taken straight from `file.path` of a `WorkingDirectoryFileChange`/`CommittedFileChange`, which ultimately originates from `git status --porcelain=2 -z` output parsed by `parsePorcelainStatus` in `app/src/lib/status-parser.ts`. That parser only splits fields, extracts the path, and never inspects file mode/type for symlinks (mode `120000` is tracked and even exercised in the "parses a typechange" test at `app/test/unit/status-parser-test.ts:109-118`, showing the parser is mode-agnostic).

The resolved path is then read with no boundary check: [1](#0-0) 

`readFile` in Node.js follows symlinks by default. If the working tree, populated from a cloned/fetched/merged repository, contains a tracked symlink at the conflicted path (e.g. `conflict-file -> /home/user/.ssh/id_rsa` or `-> ../../outside-repo/secret.txt`), the call `readFile(Path.join(repository.path, filePath), 'utf8')` will transparently follow that symlink and load the target file's contents as `baseContent`. That content is then used to build a diff (`git diff --no-index` against temp files) and surfaced to the renderer via `IResolutionDiff.oldContents`, which is displayed in `CopilotConflictsChanges` (`app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx:153-231`) as syntax-highlighted diff content.

No code path performs `lstat`/`realpath` containment checks or rejects symlinked conflicted files before this read — a `grep` for `realpath`/`lstat`/`isSymbolicLink` across the app source turns up nothing in the diff/status/merge pipeline.

### Impact Explanation
This is a file read outside the repository sandbox triggered purely by cloning/merging an attacker-controlled repository: the victim only has to open Desktop's conflict-resolution UI on a repository that contains a symlinked conflicted file. Depending on the target, this can expose SSH private keys, `.netrc`/credential files, or other sensitive local files inside the rendered diff view, which the user might then inadvertently share (e.g. via a screenshot, or if the “Copilot resolved content” or diff text is later copied/committed). This matches the "file read outside the repo" and, transitively, "credential exfiltration" impact classes for an unprivileged, attacker-supplied repository.

### Likelihood Explanation
Likelihood is moderate-to-high for repositories that trigger merge conflicts: an attacker who gets a victim to clone/fetch a branch and merge it (a completely normal collaboration workflow) can commit a symlink at a path that will end up conflicted (e.g. modify/modify or add/add conflict) so that `getResolutionDiff` is invoked on it through the Copilot conflict-resolution dialog. No local access, admin rights, or prior compromise is required — only that the user perform an ordinary merge/clone and open the conflict UI, which the feature actively encourages.

### Recommendation
Before calling `readFile` in `getResolutionDiff` (and any similar working-tree file reads driven by parsed git status/diff paths), resolve the real path with `fs.promises.realpath` (or `lstat` to detect symlinks) and verify it stays within `repository.path` using a proper path-containment check; reject or warn on symlinked/out-of-tree targets instead of silently following them. The same guard should be applied wherever `Path.join(repository.path, <status/diff derived path>)` is passed to file system read APIs.

### Proof of Concept
1. Attacker creates a repo with two branches that both add a file `evil` at the same path, but on the attacker's branch `evil` is a symlink to `/home/victim/.ssh/id_rsa` (or any sensitive absolute/relative path reachable from the victim's checkout).
2. Victim clones the repo, checks out their own branch, and merges the attacker's branch, producing a modify/modify (or add/add) conflict on `evil`, with the working-tree entry for `evil` remaining a symlink as written by git.
3. Victim opens the Copilot conflict-resolution dialog; `CopilotConflictsChanges.loadDiffForFile` calls `getResolutionDiff(repository, 'evil', { stage: 'ours' | 'theirs' })`.
4. Inside `getResolutionDiff`, `readFile(Path.join(repository.path, 'evil'), 'utf8')` follows the symlink and loads `id_rsa`'s contents into `baseContent`, which is diffed and rendered in the UI, exposing the private key content to the user's screen/diff view outside the intended repository sandbox.

### Citations

**File:** app/src/lib/git/diff.ts (L456-463)
```typescript
  // Always diff against the working-tree file (which still has conflict
  // markers). This gives a consistent baseline for all three resolution
  // choices (Copilot, current, incoming) so the user sees exactly what each
  // option changes relative to the file's current state on disk.
  const baseContent = await readFile(
    Path.join(repository.path, filePath),
    'utf8'
  )
```
