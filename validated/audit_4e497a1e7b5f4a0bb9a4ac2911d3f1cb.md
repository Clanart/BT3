## Title
Symlink path‑escape allows reading files outside the repository during conflict‑resolution diff rendering — ([File: app/src/lib/git/diff.ts])

### Summary
`getResolutionDiff` in `app/src/lib/git/diff.ts` reads the on‑disk conflicted file with a plain `Path.join(repository.path, filePath)` + `readFile`, with no symlink/traversal check. The sibling feature that reads conflicted files for the same purpose, `buildConflictContext` in `app/src/lib/copilot-conflict-context.ts`, explicitly guards the exact same read against symlink/path‑escape using `resolveWithin`. The guard was added to one code path but not the other, so the same class of attack the guard was built to stop is still reachable through `getResolutionDiff`.

### Finding Description
When a user merges, rebases, or cherry‑picks from an attacker‑influenced branch/remote, a conflicting path can be checked out by Git as a **symlink** (e.g. one side of the merge is a symlink, the other a regular file — a type‑change conflict). Desktop's conflict-resolution UI (`copilot-conflicts-changes.tsx`) then calls: [1](#0-0) 

which invokes `getResolutionDiff(repository, file.path, { stage: 'ours' | 'theirs' }, ...)`. Inside `getResolutionDiff`: [2](#0-1) 

the "base" content is read via `readFile(Path.join(repository.path, filePath), 'utf8')` — a plain join with **no** call to `resolveWithin`. Node's `readFile` follows symlinks, so if `filePath` on disk is a symlink pointing outside the repository (e.g. to `~/.ssh/id_rsa`, a browser profile, or any file readable by the OS user), this call reads and returns that file's content instead of a file inside the repo.

Compare this to the nearly identical, but properly guarded, code path added for Copilot's conflict context builder: [3](#0-2) 

which explicitly resolves the path via `resolveWithin(workingDirectory, file.path)` and rejects results outside the repo root — precisely to stop "path traversal and symlink escapes," as its own comment states. `resolveWithin` is unit-tested specifically for this scenario: [4](#0-3) 

`getResolutionDiff`'s file paths are sourced from `WorkingDirectoryFileChange.path` for conflicted files, which is derived from `git status` parsing of the merge/rebase/cherry-pick in progress — i.e., content and structure that is directly influenced by the branch/commit the user merged from, which can be fully attacker‑controlled (a malicious fork, PR branch, or remote).

### Impact Explanation
This allows reading and displaying the contents of an arbitrary file on the user's filesystem (any file readable by the OS user running Desktop) inside the app's diff viewer, purely by getting the victim to merge/rebase/cherry-pick a crafted branch that introduces a symlink at a conflicting path. This satisfies the "file... read outside the repo" impact category: sensitive local files (SSH keys, cloud credentials, browser data, etc.) can be exfiltrated into the UI and, depending on which resolution path is chosen (`{ content: resolution.resolvedContent }` mode also reads through the same unguarded path for the "on-disk" baseline), potentially into further processing.

### Likelihood Explanation
The attacker only needs to control a git ref that the victim merges/rebases/cherry-picks against (a fork, PR head, or any remote branch) — no local access, no admin rights, no pre-existing malware, and no unnatural user steps beyond the ordinary "resolve merge conflicts" workflow that GitHub Desktop is built around. The vulnerable and the fixed code paths sit side-by-side in the same conflict-resolution feature set, indicating the gap is a real oversight rather than a hypothetical.

### Recommendation
Route the on-disk read in `getResolutionDiff` (and any other direct `Path.join(repository.path, filePath)` + `readFile` sites handling conflicted/attacker-influenced paths, e.g. `app/src/lib/git/diff.ts` around line 460) through `resolveWithin` (from `app/src/lib/path.ts`), rejecting or safely handling paths that resolve outside the repository root, exactly as done in `buildConflictContext`.

### Proof of Concept
1. Attacker creates a branch/fork where a file `secret-look` is a symlink to `/Users/victim/.ssh/id_rsa` (or any sensitive path).
2. Victim has a regular-file modification to `secret-look` on their own branch.
3. Victim merges the attacker's branch in Desktop; Git reports a type-change conflict, leaving the symlink on disk at `secret-look`.
4. Victim opens the merge-conflicts dialog and picks "ours"/"theirs" (or triggers the Copilot conflict resolution UI) for `secret-look`.
5. `getResolutionDiff(repository, 'secret-look', ...)` executes `readFile(Path.join(repository.path, 'secret-look'), 'utf8')`, which follows the symlink and returns the contents of `~/.ssh/id_rsa`, displayed as the "diff" in the UI.

### Citations

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx (L153-187)
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
            fileContents: this.buildFileContents(file, result),
          })
        }
      } catch (e) {
        log.error('Failed to compute resolution diff', e)
        if (this.mounted && requestId === this.diffRequestId) {
          this.setState({ diff: null, fileContents: null, diffError: true })
        }
      }
      return
```

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

**File:** app/test/unit/path-test.ts (L65-78)
```typescript
    if (!__WIN32__) {
      it('fails for paths that use a symlink to traverse outside of the root', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          assert((await resolveWithin(tempDir, symlinkName)) === null)
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })
```
