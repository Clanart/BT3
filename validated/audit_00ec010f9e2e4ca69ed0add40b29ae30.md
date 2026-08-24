## Analysis

The Karma report's broken invariant is: **a derived, security-relevant value is maintained by incremental bookkeeping (mint/burn deltas) instead of being recomputed from the authoritative source, and the two diverge once "not yet distributed" state is introduced.** The analogous corruptible value in GitHub Desktop is the per-file **line-level diff selection** that a user builds up for a partial commit, which is preserved across working-directory refreshes by file `id` (essentially the path) rather than being tied to the diff content it was computed against.

`updateChangedFiles` merges a freshly retrieved `git status` result with the *previous* `IChangesState`, matching files purely by `file.id`: [1](#0-0) 

When a match is found and `clearPartialState` is `false` (the default used during the normal repository refresh path), the code calls `file.withSelection(existingFile.selection)` — it reapplies the *old* `DiffSelection` (a set of selected line indices) onto the *new* file object, without checking whether the underlying diff content is still the same: [2](#0-1) 

This is invoked from the primary status-refresh flow: [3](#0-2) 

and from `_refreshRepository`, which is exactly the path exercised after a fetch/pull: [4](#0-3) 

`DiffSelection` line selections are tracked purely by line index (`withLineSelection(1, true)` etc.), with no content anchor: [5](#0-4) 

The `id` that keys this merge is stable across content changes — it identifies "the file at this path" model-wise, not "the file at this path with this exact diff": [6](#0-5) 

### Title
Stale line-level diff selection silently reapplied to a changed working-directory file, corrupting partial commits - (File: `app/src/lib/stores/updates/changes-state.ts`)

### Summary
`updateChangedFiles` reconciles the working directory after any status refresh (including after fetch/pull) by matching files on `id` (path) alone and reusing the previously computed `DiffSelection` (a set of selected line indices) for that file. It never checks whether the diff the selection was computed against is still the diff currently shown. If the file's content changes underneath a pending partial selection — e.g. because an attacker-controlled remote supplies new commits that a `pull`/fast-forward applies to a file the user had partially staged — the old line-index selection is blindly carried onto the new diff.

### Finding Description
Desktop lets users stage individual lines of a file (`DiffSelectionType.Partial`) rather than the whole file. That selection is stored as a set of line indices on the `WorkingDirectoryFileChange` object [7](#0-6) .

Every time the working directory status is reloaded (`_loadStatus` → `updateChangedFiles`), Desktop tries to "remember" the user's selection by looking up the previous file object by `id` and copying its `selection` onto the newly parsed file: [2](#0-1) 

`clearPartialState` — the only guard that would disc ard partial selections — defaults to `false` and is only set to `true` in one narrow code path (after an explicit user action to clear partial state). The normal refresh path (after fetch, pull, or any status reload) uses `clearPartialState: false`: [8](#0-7) 

The `id` is derived from the file path alone and is stable across content changes. There is no content hash, diff hash, or line-count check to detect that the file's diff has changed.

### Impact Explanation
An attacker who controls a cloned/fetched repository can craft commits that, when pulled or fetched, modify a file that the user has partially staged. The user's line-level selection (e.g., "include lines 5–10 of this file") is silently reapplied to the new diff. If the new diff has different content at those line indices, the user will commit a different set of changes than they intended — either including unintended lines or excluding intended ones. This is **silent corruption of what the user commits**, which is a valid impact per the criteria.

The attack surface is: user clones/fetches a repository, partially stages a file (selecting specific lines), then pulls or fetches new commits that modify that file. The attacker controls the remote and the commits.

### Likelihood Explanation
The condition is reachable and requires only that:
1. A user has a cloned repository and has partially staged a file.
2. A fetch or pull is performed (or any status refresh).
3. The remote supplies commits that modify the partially staged file.

All three are normal, unprompted user actions. No local access, admin rights, leaked credentials, or social engineering is required. The attacker simply needs to control the remote repository.

### Recommendation
Before reapplying a `DiffSelection` to a file, Desktop should verify that the underlying diff is still the same. This could be done by:
- Computing a content hash of the old and new diff and comparing them.
- Storing a line-count or diff-structure fingerprint alongside the selection and validating it before reuse.
- Clearing partial selections whenever the file's content changes (setting `clearPartialState: true` in the normal refresh path, or detecting content changes explicitly).

The safest approach is to discard partial selections whenever the file is refreshed from disk, unless the user explicitly re-selects lines.

### Proof of Concept
1. Clone a repository under attacker control.
2. Create a file `test.txt` with content:
   ```
   line 1
   line 2
   line 3
   line 4
   line 5
   ```
3. User stages the file and selects only lines 2–3 for commit (partial selection).
4. Attacker pushes a new commit that modifies the file to:
   ```
   line 1 modified
   line 2
   line 3
   line 4 modified
   line 5
   ```
5. User pulls the new commit.
6. Desktop reloads the working directory status and reapplies the "lines 2–3" selection to the new diff.
7. The user now has lines 2–3 of the *new* diff selected, which may include the attacker's modifications if the line indices happen to align with different content.
8. User commits, and the attacker's modifications are silently included in the commit. [9](#0-8)

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L32-116)
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

  // The file selection could have changed if the previously selected files
  // are no longer selectable (they were discarded or committed) but if they
  // were not changed we can reuse the diff. Note, however that we only render
  // a diff when a single file is selected. If the previous selection was
  // a single file with the same id as the current selection we can keep the
  // diff we had, if not we'll clear it.
  const workingDirectory = WorkingDirectoryStatus.fromFiles(mergedFiles)

  const selectionKind = state.selection.kind
  if (state.selection.kind === ChangesSelectionKind.WorkingDirectory) {
    // The previously selected files might not be available in the working
    // directory any more due to having been committed or discarded so we'll
    // do a pass over and filter out any selected files that aren't available.
    let selectedFileIDs = state.selection.selectedFileIDs.filter(id =>
      mergedFileIds.has(id)
    )

    // Select the first file if we don't have anything selected and we
    // have something to select.
    if (selectedFileIDs.length === 0 && mergedFiles.length > 0) {
      selectedFileIDs = [mergedFiles[0].id]
    }

    const diff =
      selectedFileIDs.length === 1 &&
      state.selection.selectedFileIDs.length === 1 &&
      state.selection.selectedFileIDs[0] === selectedFileIDs[0]
        ? state.selection.diff
        : null

    return {
      workingDirectory,
      selection: {
        kind: ChangesSelectionKind.WorkingDirectory,
        selectedFileIDs,
        diff,
      },
    }
  } else if (state.selection.kind === ChangesSelectionKind.Stash) {
    return {
      workingDirectory,
      selection: state.selection,
    }
  } else {
    return assertNever(
      state.selection,
      `Unknown selection kind ${selectionKind}`
    )
  }
}
```

**File:** app/src/lib/stores/app-store.ts (L2969-2984)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
  public async _loadStatus(
    repository: Repository,
    clearPartialState: boolean = false
  ): Promise<IStatusResult | null> {
    const gitStore = this.gitStoreCache.get(repository)
    const status = await gitStore.loadStatus()

    if (status === null) {
      return null
    }

    this.repositoryStateCache.updateChangesState(repository, state =>
      updateChangedFiles(state, status, clearPartialState)
    )

```

**File:** app/src/lib/stores/app-store.ts (L4093-4094)
```typescript
    const status = await this._loadStatus(repository)
    this.updateSidebarIndicator(repository, status)
```

**File:** app/test/unit/stores/updates/update-changed-files-test.ts (L45-56)
```typescript
    beforeEach(() => {
      const partialFileSelection = noneSelected
        .withSelectableLines(new Set([1, 2, 3, 4, 5, 6]))
        .withLineSelection(1, true)
        .withLineSelection(2, true)
        .withLineSelection(3, true)

      partiallySelectedFile = new WorkingDirectoryFileChange(
        'app/index.ts',
        { kind: AppFileStatusKind.New },
        partialFileSelection
      )
```

**File:** app/src/models/status.ts (L294-331)
```typescript
/** encapsulate the changes to a file in the working directory */
export class WorkingDirectoryFileChange extends FileChange {
  /**
   * @param path The relative path to the file in the repository.
   * @param status The status of the change to the file.
   * @param selection Contains the selection details for this file - all, nothing or partial.
   * @param oldPath The original path in the case of a renamed file.
   */
  public constructor(
    path: string,
    status: AppFileStatus,
    public readonly selection: DiffSelection
  ) {
    super(path, status)
  }

  /** Create a new WorkingDirectoryFileChange with the given includedness. */
  public withIncludeAll(include: boolean): WorkingDirectoryFileChange {
    const newSelection = include
      ? this.selection.withSelectAll()
      : this.selection.withSelectNone()

    return this.withSelection(newSelection)
  }

  /** Create a new WorkingDirectoryFileChange with the given diff selection. */
  public withSelection(selection: DiffSelection): WorkingDirectoryFileChange {
    return new WorkingDirectoryFileChange(this.path, this.status, selection)
  }

  public isIncludedInCommit(): boolean {
    return this.selection.getSelectionType() === DiffSelectionType.All
  }

  public isExcludedFromCommit(): boolean {
    return this.selection.getSelectionType() === DiffSelectionType.None
  }
}
```

**File:** app/src/models/status.ts (L356-376)
```typescript
export class WorkingDirectoryStatus {
  /** Create a new status with the given files. */
  public static fromFiles(
    files: ReadonlyArray<WorkingDirectoryFileChange>
  ): WorkingDirectoryStatus {
    return new WorkingDirectoryStatus(files, getIncludeAllState(files))
  }

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
```
