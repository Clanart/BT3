## Finding Summary

I was not able to confirm a full, concrete reproduction path from local code evidence alone. I found one structural analog worth flagging, but I could not verify it end-to-end (in particular, how a duplicate `id`/`path` can actually reach `WorkingDirectoryStatus.fromFiles()` from attacker-controlled repository state), so I'm reporting it with explicit caveats rather than as a confirmed vulnerability.

### Title
Potential desync between `files` array and `fileIxById` index map in `WorkingDirectoryStatus` - (File: `app/src/models/status.ts`)

### Summary
`WorkingDirectoryStatus` mirrors the exact "list + index map" pattern from the CometBFT mempool bug: an ordered array (`files`) plus a `Map<string, number>` (`fileIxById`) that is supposed to track each file's index in the array by id. [1](#0-0) 

### Finding Description
The constructor builds the index map with a simple `forEach`:
```
files.forEach((f, ix) => this.fileIxById.set(f.id, ix))
```
If the `files` array ever contains two entries with the same `id` (Desktop's `WorkingDirectoryFileChange.id`, which is derived from the file path), the map is last-write-wins: only the index of the *last* duplicate is retained. The first duplicate remains physically present in `files` — and is therefore still iterated by `getIncludeAllState()` (which decides checkbox/include state used when building a commit) — but becomes permanently unreachable via `findFileWithID` / `findFileIndexByID`, which every UI selection, diff-loading, and "click a row" path relies on. [2](#0-1) 

Notably, the git-status parsing code is aware that colliding-path entries can arise (e.g. a staged delete plus an untracked file at the same path) and explicitly patches around it by deleting the stale map entry before re-inserting: [3](#0-2) 

This confirms the invariant ("one `WorkingDirectoryFileChange` per id in `files`") is enforced by ad-hoc, per-call-site logic rather than by the `WorkingDirectoryStatus` class itself — the same root cause pattern as the CometBFT mempool bug, where the list/map invariant was maintained by convention rather than by a data structure that couldn't get out of sync.

Downstream, this array is repeatedly mutated with plain `.map()`/`.filter()` transforms across many call sites (`_changeFileIncluded`, `updateWorkingDirectoryFileSelection`, `updateChangedFiles`, `_hideStashedChanges`, etc.), each rebuilding a new `WorkingDirectoryStatus.fromFiles(...)`: [4](#0-3) [5](#0-4) 

### Impact Explanation
If a duplicate `id` were ever introduced into `files` (e.g., through a path-collision scenario not covered by the existing dedup guard — case-insensitive filesystem collisions, rename detection producing overlapping old/new paths, or a manual conflict-resolution/stash-restore path that merges file lists from multiple sources), the effect would be:
- The orphaned duplicate's selection state (`DiffSelectionType`) would still be counted by `getIncludeAllState()` and thus silently affect what is staged for commit.
- The user-visible UI (selection, diff, checkbox toggling by id) would only ever reference the surviving duplicate, making the corrupted state invisible until the commit is created — matching the "silent corruption of what the user commits" impact class.

This would be a **High-severity** issue *if* confirmed, since it can silently alter commit contents without user awareness.

### Likelihood Explanation
**Low confidence / unconfirmed.** I could not verify, within the indexed code, a concrete attacker-controlled trigger that produces two `WorkingDirectoryFileChange` objects with the same `id` bypassing the existing dedup logic in `buildStatusMap`. The known collision case (staged-delete + untracked-same-path) is already explicitly handled. Without a confirmed bypass, this remains a structural/theoretical weakness rather than a demonstrated exploitable path.

### Recommendation
- Have a background Devin session audit all call sites that construct `WorkingDirectoryStatus.fromFiles()` (especially those combining file lists from independent sources, such as conflict-resolution flows and stash restoration) to confirm whether duplicate ids/paths can occur, particularly under case-insensitive filesystems or git rename detection.
- Regardless of whether a live trigger is found, harden `WorkingDirectoryStatus`'s constructor to assert/dedupe on construction (e.g., throw or collapse duplicates) rather than relying on caller discipline, removing the class of bug entirely.

### Proof of Concept
Not established — I could not confirm a concrete attacker-controlled input path (e.g., a crafted upstream repository/rename/stash) that produces two `WorkingDirectoryFileChange` entries sharing an `id` before it reaches `WorkingDirectoryStatus.fromFiles()`. A background Devin session with repository access should attempt to construct such a scenario (case-insensitive filesystem path collision, or a rename/stash interaction) to confirm or refute exploitability before this is treated as a confirmed finding.

### Citations

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

**File:** app/src/models/status.ts (L399-421)
```typescript
function getIncludeAllState(
  files: ReadonlyArray<WorkingDirectoryFileChange>
): boolean | null {
  if (!files.length) {
    return true
  }

  const allSelected = files.every(
    f => f.selection.getSelectionType() === DiffSelectionType.All
  )
  const noneSelected = files.every(
    f => f.selection.getSelectionType() === DiffSelectionType.None
  )

  let includeAll: boolean | null = null
  if (allSelected) {
    includeAll = true
  } else if (noneSelected) {
    includeAll = false
  }

  return includeAll
}
```

**File:** app/src/lib/git/status.ts (L320-326)
```typescript
  if (status.kind === 'untracked') {
    // when a delete has been staged, but an untracked file exists with the
    // same path, we should ensure that we only draw one entry in the
    // changes list - see if an entry already exists for this path and
    // remove it if found
    files.delete(entry.path)
  }
```

**File:** app/src/lib/stores/app-store.ts (L3900-3916)
```typescript
  private updateWorkingDirectoryFileSelection(
    repository: Repository,
    file: WorkingDirectoryFileChange,
    selection: DiffSelection
  ) {
    this.repositoryStateCache.updateChangesState(repository, state => {
      const newFiles = state.workingDirectory.files.map(f =>
        f.id === file.id ? f.withSelection(selection) : f
      )

      const workingDirectory = WorkingDirectoryStatus.fromFiles(newFiles)

      return { workingDirectory }
    })

    this.emitUpdate()
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
