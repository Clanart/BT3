## Analysis

I found a concrete Desktop analog. Confirmed evidence:

- `getResolutionDiff()` in `app/src/lib/git/diff.ts:447-463` reads a merge-conflicted file directly from the working tree with `readFile(Path.join(repository.path, filePath), 'utf8')` — no `resolveWithin`/`realpath` containment check. [1](#0-0) 
- `filePath` is the `path` of a `WorkingDirectoryFileChange`/`CommittedFileChange` that came straight from git's conflicted-file listing, and is passed by `CopilotConflictsChanges.loadDiffForFile` without any sanitization. [2](#0-1) 
- The codebase already has a hardened pattern for exactly this class of bug: `buildConflictContext()` explicitly resolves each conflicted file path through `resolveWithin(workingDirectory, file.path)` before reading it, with a comment stating this guards "against path traversal and symlink escapes (cross-platform)". [3](#0-2) 
- `resolveWithin` implements the containment check via `realpath` comparison against the repo root. [4](#0-3) 

This is a direct analog of the "state assumed safe but not re-validated" pattern in the report: one code path (`buildConflictContext`) enforces the invariant that a conflicted path resolves inside the repository, while a parallel code path (`getResolutionDiff`) that operates on the *same untrusted, repository-controlled conflict-file list* omits the check and reads whatever the resolved path points to.

### Title
Unsanitized conflicted-file path enables symlink-based file read outside the repository in Copilot conflict resolution diff - (File: app/src/lib/git/diff.ts)

### Summary
`getResolutionDiff()` builds the "base" side of a merge-conflict resolution diff by reading `Path.join(repository.path, filePath)` directly from disk, where `filePath` is a path taken from git's conflicted-file status entries — content fully controlled by whoever authored the merge conflict (a malicious/attacker-crafted repository or branch that the user merges/rebases). Unlike the sibling function `buildConflictContext()`, which validates the same kind of path through `resolveWithin()` to guard against symlink escapes, `getResolutionDiff()` performs no such validation before reading the file.

### Finding Description
A repository can contain a path component that is a symlink (e.g. a tracked/untracked directory entry `evil -> /` or `evil -> ~/.ssh`) placed as part of the merge conflict tree. When the user opens the Copilot Conflict resolution "Changes" tab and views a manually-resolved ("ours"/"theirs") conflicted file whose path traverses that symlinked directory, `CopilotConflictsChanges.loadDiffForFile` calls `getResolutionDiff(repository, file.path, { stage: choice })`, which reads:

```
const baseContent = await readFile(Path.join(repository.path, filePath), 'utf8')
``` [5](#0-4) 

`Path.join` does not resolve symlinks, and there is no subsequent check (like `realpath`-based containment) that the resulting path actually stays under `repository.path`. If the on-disk path escapes the repository via a symlinked ancestor directory, the file that gets read is outside the repository — its contents are then rendered as the "base" (old) side of the resolution diff and, via `buildFileContents`, fed into `SeamlessDiffSwitcher`'s syntax highlighting/diff view as plain text, disclosing it to the attacker-observable UI (and to Copilot if resolution is later requested with this content as context).

The existing guard elsewhere in the codebase (`resolveWithin` in `buildConflictContext`) demonstrates the app's own authors recognize this exact attack surface for conflicted files, but the guard was not applied uniformly to `getResolutionDiff`.

### Impact Explanation
This matches the "valid impact" bar: the attacker controls a cloned/fetched/merged repository object (a conflicting branch/commit containing a symlinked path), and the result is a file read outside the repository working directory, rendered into the UI. It could expose sensitive local files (SSH keys, config files, credentials) to the merge-conflict diff viewer, and if the resolved diff feeds into further automated flows (e.g., Copilot conflict resolution requests), it could exfiltrate that content to a remote model/service.

### Likelihood Explanation
Requires the victim to attempt a merge/rebase against a maliciously crafted branch/repository that produces a manual (non-Copilot) merge conflict, then to open the Copilot Conflicts "Changes" tab and select the affected file with an "ours"/"theirs" resolution choice — no unusual local access or elevated privileges needed, only normal Desktop usage of merging an untrusted branch and viewing conflict resolution. This is a plausible but non-trivial path (feature is specific to the newer Copilot conflict-resolution UI), so likelihood is moderate rather than certain.

### Recommendation
Apply the same containment check used in `buildConflictContext()` to `getResolutionDiff()`: resolve `filePath` via `resolveWithin(repository.path, filePath)` (or equivalent `realpath`-based check) before calling `readFile`, and treat a `null`/outside-root result as an unreadable/skip case rather than reading the path.

### Proof of Concept
1. Attacker prepares a branch where the merge will produce a conflict on a path such as `linkdir/target.txt`, and `linkdir` is committed/checked out as a symbolic link pointing to a sensitive location (e.g. the user's home directory or an absolute path like `/etc`), consistent with how git can materialize symlinked tree entries.
2. Victim adds/fetches this branch in GitHub Desktop and starts a merge/rebase, hitting a conflict on `linkdir/target.txt` which appears in `changesState.conflictedFiles`.
3. Victim opens the Copilot Conflicts dialog "Changes" tab; `CopilotConflictsChanges` selects the file and, since the choice is manually set to "ours"/"theirs", calls `getResolutionDiff(repository, 'linkdir/target.txt', { stage: 'ours' })`. [6](#0-5) 
4. `getResolutionDiff` executes `readFile(Path.join(repository.path, 'linkdir/target.txt'), 'utf8')`, which — because `linkdir` is a symlink — actually resolves to a file outside the repository (e.g. `~/target.txt` or `/etc/target.txt`), and its content is displayed as the diff base and rendered in the UI. [1](#0-0)

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

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx (L153-177)
```typescript
  private async loadDiffForFile(file: CommittedFileChange) {
    const requestId = ++this.diffRequestId
    const choice = getResolutionChoiceForFile(
      file.path,
      this.props.manualResolutions
    )

    if (choice === 'ours' || choice === 'theirs') {
      this.setState({
        diff: null,
        fileContents: null,
        noResolution: false,
        diffError: false,
      })
      try {
        const result = await getResolutionDiff(
          this.props.repository,
          file.path,
          { stage: choice },
          this.state.hideWhitespaceInDiff
        )

        if (this.mounted && requestId === this.diffRequestId) {
          this.setState({
            diff: result.diff,
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-407)
```typescript
      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }
```

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```
