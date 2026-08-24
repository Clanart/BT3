### Title
`FileChange.id` collision via unescaped `+`-concatenated paths corrupts per-file selection/diff state - (File: `app/src/models/status.ts`)

### Summary
`FileChange` builds its identity string by naively concatenating the file `kind` and `path` (and `oldPath` for renames/copies) with `+` as a separator, with no escaping of `+` characters that may legally appear in a real filename: [1](#0-0) 

This id is then used as the sole key for every "find/replace this file" operation in the app: `WorkingDirectoryStatus.fileIxById`/`findFileWithID` [2](#0-1) , the previous-selection merge in `updateChangedFiles` [3](#0-2) , and the "replace file object by id" pattern used when persisting a freshly-loaded diff/selection back into the working directory [4](#0-3) . Since `+` is a valid filename byte on all common filesystems and Git itself, an attacker who controls the contents of a cloned/fetched repository can craft two distinct paths whose `kind+path[+oldPath]` concatenation is identical, forcing these lookups/replacements to treat two different files as one.

### Finding Description
The broken invariant is "each working-directory file has a globally unique identity used for selection, diff caching, and state merging." That invariant relies on the id string being injective with respect to `(kind, path, oldPath)`, but the id is built with plain string concatenation and `+` as a delimiter with no escaping:

```
this.id = `${status.kind}+${path}+${status.oldPath}`   // renamed/copied
this.id = `${status.kind}+${path}`                      // everything else
```

Because `+` is unescaped, two crafted (path, oldPath) pairs of the *same* Git status `kind` can produce an identical id, e.g. a renamed entry with `path = "a+b", oldPath = "c"` and another renamed entry with `path = "a", oldPath = "b+c"` both yield `"Renamed+a+b+c"`. An attacker only needs to control the file/directory names that end up in the victim's working tree (via a branch/PR the victim checks out, a submodule, or files delivered by a `git pull`/`fetch`+checkout) to set this up — no local access, admin rights, or prior compromise required.

Once two files collide on id:
- `WorkingDirectoryStatus`'s `fileIxById` map (`files.forEach((f, ix) => this.fileIxById.set(f.id, ix))`) can only point at one of the two colliding entries, so `findFileWithID` silently resolves to the "wrong" file for the other.
- `updateChangedFiles`'s `filesByID.get(file.id)` merge step will hand the *same* previous selection/partial-line state to both colliding files when a new status is computed, so toggling or clearing one file's checkbox state can be applied to a completely unrelated file.
- The diff-refresh path in `app-store.ts` explicitly does `changesState.workingDirectory.files.map(f => f.id === selectedFile.id ? selectedFile : f)` — if two files share an id, both list entries get replaced by the single "selected" file object, meaning UI code and downstream commit-selection state can end up showing/holding one file's diff or inclusion state for what is actually a different file on disk.

None of the existing guards (`caseInsensitiveCompare` sort, `mergedFileIds` Set, `arrayEquals` staleness checks) validate id uniqueness before performing these keyed lookups/merges — they all assume `id` is unique per file.

### Impact Explanation
This lets an attacker-controlled repository silently corrupt what the user believes they are including/excluding from a commit or reviewing before committing/pushing: the checkbox state and rendered diff for a sensitive/malicious file can become entangled with an unrelated, similarly-crafted file, so the user may review one diff while actually committing the content of the colliding file, or unintentionally include/exclude a file they didn't intend to. This falls squarely in the "silent corruption of what the user commits or pushes" impact bucket defined for this analog search.

### Likelihood Explanation
Exploitation requires only that the victim check out/clone/fetch a repository containing two attacker-crafted paths whose Git-status kind/path/oldPath combination collides under the `+`-concatenation scheme (trivial to construct, since `+` is unrestricted in Git paths) and that the victim then interacts with the Changes list (select/toggle/view diff) for those files — an entirely normal workflow, not a special or unnatural user action. No credentials, admin rights, or local/physical access are needed.

### Recommendation
Replace the ad-hoc `+`-joined string with a collision-free id, e.g. JSON-encode the tuple `(kind, path, oldPath)` or use a delimiter-safe encoding (percent/URI-encode each component before joining, or hash the tuple with a fixed-length digest). Add a unit test asserting that `FileChange.id` is injective for paths containing the delimiter character(s).

### Proof of Concept
1. Craft a Git history where, from the user's perspective, a rename is detected from `oldPath = "c"` to `path = "a+b"`, and, in the same status snapshot (e.g., a copy/second rename in the same commit range or a follow-up working-tree edit), another entry renames `oldPath = "b+c"` to `path = "a"`.
2. Both produce `FileChange.id === "Renamed+a+b+c"` per the concatenation in `app/src/models/status.ts` lines 263-275.
3. In the Changes list, select/exclude one of the two files. Because `WorkingDirectoryStatus.findFileWithID`/`fileIxById` (status.ts 364-397) and the id-based `.map(f => f.id === selectedFile.id ? selectedFile : f)` replace in `app-store.ts` (3495-3512) key exclusively off this colliding id, the selection/diff state intended for one file is applied to (or displayed for) the other file as well.
4. The user commits believing they reviewed/selected File A, while File B's staged content (or vice versa) is what actually gets included, without any warning from the app.

### Citations

**File:** app/src/models/status.ts (L263-275)
```typescript
  public constructor(
    public readonly path: string,
    public readonly status: AppFileStatus
  ) {
    if (
      status.kind === AppFileStatusKind.Renamed ||
      status.kind === AppFileStatusKind.Copied
    ) {
      this.id = `${status.kind}+${path}+${status.oldPath}`
    } else {
      this.id = `${status.kind}+${path}`
    }
  }
```

**File:** app/src/models/status.ts (L364-397)
```typescript
  private readonly fileIxById = new Map<string, number>()
  /**
   * @param files The list of changes in the repository's working directory.
   * @param includeAll Update the include checkbox state of the form.
   *                   NOTE: we need to track this separately to the file list selection
   *                         and perform two-way binding manually when this changes.
   */
  private constructor(
    public readonly files: ReadonlyArray<WorkingDirectoryFileChange>,
    public readonly includeAll: boolean | null = true
  ) {
    files.forEach((f, ix) => this.fileIxById.set(f.id, ix))
  }

  /**
   * Update the include state of all files in the working directory
   */
  public withIncludeAllFiles(includeAll: boolean): WorkingDirectoryStatus {
    const newFiles = this.files.map(f => f.withIncludeAll(includeAll))
    return new WorkingDirectoryStatus(newFiles, includeAll)
  }

  /** Find the file with the given ID. */
  public findFileWithID(id: string): WorkingDirectoryFileChange | null {
    const ix = this.fileIxById.get(id)
    return ix !== undefined ? this.files[ix] || null : null
  }

  /** Find the index of the file with the given ID. Returns -1 if not found */
  public findFileIndexByID(id: string): number {
    const ix = this.fileIxById.get(id)
    return ix !== undefined ? ix : -1
  }
}
```

**File:** app/src/lib/stores/updates/changes-state.ts (L32-65)
```typescript
export function updateChangedFiles(
  state: IChangesState,
  status: IStatusResult,
  clearPartialState: boolean
): ChangedFilesResult {
  // Populate a map for all files in the current working directory state
  const filesByID = new Map<string, WorkingDirectoryFileChange>()
  state.workingDirectory.files.forEach(f => filesByID.set(f.id, f))

  // Attempt to preserve the selection state for each file in the new
  // working directory state by looking at the current files
  const mergedFiles = status.workingDirectory.files
    .map(file => {
      const existingFile = filesByID.get(file.id)
      if (existingFile) {
        if (clearPartialState) {
          if (
            existingFile.selection.getSelectionType() ===
            DiffSelectionType.Partial
          ) {
            return file.withIncludeAll(false)
          }
        }

        return file.withSelection(existingFile.selection)
      } else {
        return file
      }
    })
    .sort((x, y) => caseInsensitiveCompare(x.path, y.path))

  // Collect all the currently available file ids into a set to avoid O(N)
  // lookups using .find on the mergedFiles array.
  const mergedFileIds = new Set(mergedFiles.map(x => x.id))
```

**File:** app/src/lib/stores/app-store.ts (L3495-3512)
```typescript
    const newSelection =
      currentlySelectedFile.selection.withSelectableLines(selectableLines)
    const selectedFile = currentlySelectedFile.withSelection(newSelection)
    const updatedFiles = changesState.workingDirectory.files.map(f =>
      f.id === selectedFile.id ? selectedFile : f
    )
    const workingDirectory = WorkingDirectoryStatus.fromFiles(updatedFiles)

    const selection: ChangesWorkingDirectorySelection = {
      ...changesState.selection,
      diff,
    }

    this.repositoryStateCache.updateChangesState(repository, () => ({
      selection,
      workingDirectory,
    }))
    this.emitUpdate()
```
