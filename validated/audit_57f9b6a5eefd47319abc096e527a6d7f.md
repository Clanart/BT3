## Analysis

The Sherlock report's broken invariant is: **two logically distinct entries (NFTs) alias to the same physical resource (tokenId)**, so a `mapping(tokenId => recipient)` write for the second entry clobbers/loses data associated with the first, and the code proceeds to transfer/write without detecting the collision.

The equivalent aliasing hazard in GitHub Desktop is in the Copilot merge-conflict-resolution write path, `_applyCopilotConflictResolutions` in `app-store.ts`. Conflicted files are tracked by their raw repository-relative path string, and Copilot's returned resolutions are validated for exact-string duplicates only — not for paths that are distinct as git objects but alias to the same file on disk (paths differing only in letter case, which on the case-insensitive filesystems Desktop supports by default — macOS APFS and Windows — resolve to one physical file).

### Title
Copilot conflict resolutions for case-colliding paths silently clobber each other's content before staging - (File: app/src/lib/stores/app-store.ts)

### Summary
Git itself is case-sensitive and can store two distinct blobs such as `Notes.txt` and `notes.txt` in a tree. When a repository fetched/merged by the victim contains a merge conflict crafted so that both differently-cased paths are modified on both sides, Desktop's status parser and Copilot conflict pipeline treat them as two independent conflicted files keyed by their exact path string. `validateResolutionPaths` only rejects duplicates when the path strings are byte-identical, so two case-variant paths pass validation as "distinct" resolutions.

### Finding Description
`getStatus`/`buildStatusMap` in `app/src/lib/git/status.ts` builds a `Map<string, WorkingDirectoryFileChange>` keyed by the exact path string returned by `git status --porcelain=2`, so `Notes.txt` and `notes.txt` are two entries. [1](#0-0) [2](#0-1) 

The Copilot resolution pipeline validates the model's returned per-file resolutions only against a `Set<string>` of exact path strings, and rejects duplicates only when `returnedPaths.size !== resolutions.length` — a case-variant pair does not collide in this check. [3](#0-2) 

When the user clicks "Continue Merge", `_applyCopilotConflictResolutions` iterates the resolutions sequentially, resolves each path independently with `resolveWithin(repository.path, resolution.path)`, and calls `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` for each: [4](#0-3) 

On a case-insensitive filesystem, `resolveWithin(repository.path, "Notes.txt")` and `resolveWithin(repository.path, "notes.txt")` resolve to the *same inode*. The second `writeFile` call silently overwrites the content written for the first path. Both paths are then pushed into `pathsToStage` and staged with a single `git add -- Notes.txt notes.txt` call: [5](#0-4) 

`git add` reads whatever is currently on disk for each pathname it is given. Since both pathnames now point at the same on-disk content (the last resolution written), git will stage the **same content under both distinct index paths** — meaning the commit records `Notes.txt` as containing content Copilot generated for `notes.txt` (or vice versa), even though the two resolutions dialog showed the user two different, independently-reasoned resolutions.

### Impact Explanation
This is silent corruption of what the user commits and pushes: the value actually committed for one of the two conflicted paths is not the content the user reviewed and approved in the Copilot resolution dialog, but the other file's resolved content. There is no error, warning, or diff check preventing this — the corrupted commit is created and can be pushed upstream, propagating incorrect file content under a legitimate-looking path with no user-visible indication of the collision.

### Likelihood Explanation
The attacker needs only to be the author of a branch that gets merged (a normal, expected Desktop workflow), crafting a conflicting pair of paths that differ solely by case on both the "ours" and "theirs" side. Desktop defaults to case-insensitive filesystems on both of its two most common platforms (macOS, Windows), so the aliasing condition is the common case, not an edge case requiring unusual host configuration. The only extra step is that the user chooses "Resolve with Copilot" instead of manual resolution, which is an intended, promoted feature of the app, not an unnatural step.

### Recommendation
In `validateResolutionPaths` (and in the file-conflict enumeration in `copilot-conflict-context.ts`/`buildConflictContext`), detect and reject/flag conflicted-file sets whose paths alias to the same filesystem resource under the platform's case-sensitivity rules (e.g., compare `Path.normalize(path).toLowerCase()` when `__WIN32__` or when running on a detected case-insensitive volume, similar to the normalization already used in `matchExistingRepository`). When a collision is found, stop and surface both paths to the user for manual resolution rather than writing/staging them independently. [6](#0-5) 

### Proof of Concept
1. Attacker creates a repository with a branch `feature` where a file `notes.txt` exists, and another branch `main` where a file `Notes.txt` (different case) exists with conflicting content changes relative to a common ancestor that also touches both names, so that merging `feature` into `main` produces two separate conflicted entries: `Notes.txt` and `notes.txt`.
2. Victim, on macOS or Windows (case-insensitive filesystem, Desktop's default supported platforms), fetches and merges the branch in GitHub Desktop, hitting conflicts.
3. Victim clicks "Resolve with Copilot". Copilot returns two independent resolutions, one for `Notes.txt` and one for `notes.txt`; `validateResolutionPaths` passes because the two strings are not byte-identical. [7](#0-6) 
4. Victim clicks "Continue Merge". `_applyCopilotConflictResolutions` writes `Notes.txt`'s resolved content, then writes `notes.txt`'s resolved content to the same on-disk file (case-insensitive alias), then stages both paths with `git add -- Notes.txt notes.txt`. [5](#0-4) 
5. The resulting commit records `Notes.txt` with the content actually intended for `notes.txt` (last-write-wins), silently diverging from what the victim reviewed and approved in the Copilot result dialog before pushing.

### Citations

**File:** app/src/lib/git/status.ts (L250-254)
```typescript
  // Map of files keyed on their paths.
  const files = entries.reduce(
    (files, entry) => buildStatusMap(files, entry, conflictDetails),
    new Map<string, WorkingDirectoryFileChange>()
  )
```

**File:** app/src/lib/git/status.ts (L345-349)
```typescript
  files.set(
    entry.path,
    new WorkingDirectoryFileChange(entry.path, appStatus, selection)
  )
  return files
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L473-495)
```typescript
export function validateResolutionPaths(
  resolutions: ReadonlyArray<IRawFileResolution>,
  expectedFiles: ReadonlyArray<IFileConflictContext>
): void {
  const expectedPaths = new Set(expectedFiles.map(f => f.path))
  const expectedHunkCounts = new Map(
    expectedFiles.map(f => [f.path, f.hunks.length])
  )
  const returnedPaths = new Set(resolutions.map(r => r.path))

  for (const path of returnedPaths) {
    if (!expectedPaths.has(path)) {
      throw new CopilotValidationError(
        `Copilot returned resolution for unexpected file: ${path}`
      )
    }
  }

  if (returnedPaths.size !== resolutions.length) {
    throw new CopilotValidationError(
      'Copilot returned duplicate file paths in resolutions'
    )
  }
```

**File:** app/src/lib/stores/app-store.ts (L7233-7267)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }

      // If the user resolved this file externally (e.g. in their editor) while
      // the result dialog was open, git status will report it with no remaining
      // conflict markers. Overwriting it with Copilot's stored content would
      // silently clobber their work, so skip it and let their resolution stand.
      // This mirrors how the manual conflicts dialog determines a file is
      // resolved (`hasUnresolvedConflicts`).
      const onDiskFile = state.changesState.workingDirectory.files.find(
        f => f.path === resolution.path
      )
      if (
        onDiskFile !== undefined &&
        isConflictedFileStatus(onDiskFile.status) &&
        !hasUnresolvedConflicts(onDiskFile.status)
      ) {
        continue
      }

      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
```

**File:** app/src/lib/repository-matching.ts (L58-64)
```typescript
  // Windows is guaranteed to be case-insensitive so we can be a bit less strict
  const normalize = __WIN32__
    ? (p: string) => Path.normalize(p).toLowerCase()
    : (p: string) => Path.normalize(p)

  const needle = normalize(path)
  return repos.find(r => normalize(r.path) === needle)
```
