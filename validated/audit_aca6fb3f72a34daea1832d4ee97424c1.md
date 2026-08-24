### Title
Path traversal / symlink escape in `getResolutionDiff` reads files outside repository - (File: `app/src/lib/git/diff.ts`)

### Summary
`getResolutionDiff` reads the "base" content for a conflict-resolution diff directly from disk with `Path.join(repository.path, filePath)` and no containment check, while the functionally equivalent conflict-context builder (`buildConflictContext`) explicitly guards the same class of input with `resolveWithin`.

### Finding Description
`getResolutionDiff` computes `baseContent` via: [1](#0-0) 
This joins `repository.path` with the caller-supplied `filePath` and reads it with no traversal/symlink containment check.

`filePath` originates from `WorkingDirectoryFileChange.path` values in `conflictedFiles`, which are produced by parsing `git status` for conflicted paths — i.e., paths that exist in the working tree as a result of a merge/rebase/checkout against attacker-controlled repository content: [2](#0-1) 

By contrast, `buildConflictContext`, which reads the very same class of on-disk conflicted files for the same feature (Copilot conflict resolution), explicitly resolves the path with `resolveWithin` and rejects it if it escapes the repository root or fails to resolve safely: [3](#0-2) 

`resolveWithin` is specifically designed to catch this scenario — it normalizes the path, calls `realpath` on both the root and the resolved target, and only allows the result if the *real* (symlink-resolved) path is still under the *real* repository root: [4](#0-3) 

The existence of this dedicated helper, and its use specifically at the sibling call site in `copilot-conflict-context.ts` with a comment stating "Guard against path traversal and symlink escapes (cross-platform)," demonstrates that the project's own threat model already recognizes a conflicted-file path can be attacker-influenced (e.g., a tracked path where an intermediate directory component is a symlink placed by a malicious commit, or similar checkout-time symlink tricks) and can escape the repository if not resolved safely. `getResolutionDiff` performs the same fs read for the same feature but omits this guard.

### Impact Explanation
If a crafted upstream branch produces a conflict whose path resolves (after checkout, following an intermediate symlinked directory) outside `repository.path`, `getResolutionDiff`'s `readFile(Path.join(repository.path, filePath), 'utf8')` would read that out-of-repo file's contents into `IResolutionDiff.oldContents`. That content then flows into the diff/file-contents state shown in the Copilot conflict-resolution UI (`CopilotConflictsChanges`), disclosing local file contents that are not part of the selected repository to whatever is rendered/exposed by that dialog — matching the "Scope: High" concern in the submitted finding (reading files outside the selected repository into a diff/Copilot context).

### Likelihood Explanation
Exploitation requires the attacker to get a victim to merge/rebase/checkout a branch that produces a conflicted path which, after Git's own checkout-time symlink/path protections, still resolves outside the repo root via a symlinked intermediate directory, and then requires the victim to open the Copilot conflict-resolution dialog for that file. Modern Git versions include their own protections against writing through symlinks during checkout (`core.protectNTFS`/`core.protectHFS`/refusal of `.git`-adjacent tricks), which reduces likelihood, but this is exactly the residual risk class that `resolveWithin` was added to close for `buildConflictContext`. Since `getResolutionDiff` is a separate, unguarded code path reading the same kind of attacker-influenced path for the same feature, the inconsistency itself is the vulnerability, independent of exactly how permissive current Git checkout behavior is.

### Recommendation
Apply the same `resolveWithin` containment check used in `buildConflictContext` to `getResolutionDiff` before reading `baseContent` (and any other on-disk read keyed by `filePath` in that function), rejecting or safely handling paths that resolve outside `repository.path`.

### Proof of Concept
1. Prepare a repository and, via a crafted upstream branch/tree, produce a working-tree conflict where the conflicted path travels through an intermediate symlink that points outside the repository (subject to the local Git version's checkout protections).
2. Trigger the conflict resolution UI so `CopilotConflictsChanges.loadDiffForFile` is invoked for that `file.path`, causing a call to `getResolutionDiff(repository, filePath, { content: ... })`.
3. Observe that `readFile(Path.join(repository.path, filePath), 'utf8')` at `app/src/lib/git/diff.ts:460-463` follows the symlink and returns content from outside `repository.path`, which is then surfaced as `IResolutionDiff.oldContents` in the diff/file-contents shown to the user — unlike the equivalent path in `buildConflictContext`, which would reject it via `resolveWithin` returning `null`.

### Citations

**File:** app/src/lib/git/diff.ts (L459-463)
```typescript
  // option changes relative to the file's current state on disk.
  const baseContent = await readFile(
    Path.join(repository.path, filePath),
    'utf8'
  )
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx (L153-217)
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
    }

    const resolution = this.props.copilotResolutions?.find(
      r => r.path === file.path
    )

    if (resolution === undefined) {
      this.setState({
        diff: null,
        fileContents: null,
        noResolution: true,
        diffError: false,
      })
      return
    }

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
        { content: resolution.resolvedContent },
        this.state.hideWhitespaceInDiff
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

**File:** app/src/lib/path.ts (L36-72)
```typescript
async function _resolveWithin(
  rootPath: string,
  pathSegments: string[],
  options: {
    join: (...pathSegments: string[]) => string
    normalize: (p: string) => string
    resolve: (...pathSegments: string[]) => string
  } = Path
) {
  // An empty root path would let all relative
  // paths through.
  if (rootPath.length === 0) {
    return null
  }

  const { join, normalize, resolve } = options

  const normalizedRoot = normalize(rootPath)
  const normalizedRelative = normalize(join(...pathSegments))

  // Null bytes has no place in paths.
  if (
    normalizedRoot.indexOf('\0') !== -1 ||
    normalizedRelative.indexOf('\0') !== -1
  ) {
    return null
  }

  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
}
```
