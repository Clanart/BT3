## Title
Path traversal via crafted submodule path in diff rendering leads to filesystem operations outside the repository - (File: `app/src/lib/git/diff.ts`)

### Summary
This is the closest analog I could substantiate to the Kodiak/HoneyLocker report's underlying pattern (an unvalidated, attacker-influenced value being propagated into a security-relevant operation with no bounds check). In GitHub Desktop, `buildSubmoduleDiff` builds a `fullPath` for a submodule diff by joining `repository.path` with the git-reported submodule `path` without validating that the result stays inside the repository, and this `fullPath` is later handed to UI actions (`onOpenSubmodule`) that operate on the filesystem.

### Finding Description
`buildSubmoduleDiff` computes the submodule's on-disk path directly from git-provided data: [1](#0-0) 

`file.path` originates from parsed `git status`/`git diff` output (`convertToAppStatus` / `buildStatusMap`), which is itself derived from tree entries recorded in a cloned/fetched repository: [2](#0-1) 

The resulting `fullPath` is stored on the `ISubmoduleDiff` object and surfaced to the UI, where `SubmoduleDiff.onOpenSubmoduleClick` forwards it verbatim to `onOpenSubmodule`: [3](#0-2) 

Nowhere in this path is `path` checked for `..` segments, absolute-path prefixes, or symlink components before being joined with `repository.path` via `Path.join`. `Path.join` does not prevent traversal — a submodule path containing `..` segments (which a malicious repository could register as a gitlink/tree entry, since Git itself does not strictly forbid path components in the raw tree that a hostile repo author controls before Desktop parses it) collapses to a path outside the working directory. This mirrors the HoneyLocker bug's core flaw: a value that is supposed to represent "the state of an external system" (LP amount actually withdrawn / a path actually inside the repo) is used unconditionally in a follow-on operation without re-validating it against reality.

### Impact Explanation
If `fullPath` escapes the repository boundary, any consumer of `onOpenSubmodule` (e.g., "reveal in Explorer/Finder" or "open in external editor") would operate on an attacker-chosen path outside the intended repository sandbox. Depending on the concrete downstream handler (not fully inspectable in the indexed code, see caveat below), this could range from opening an unexpected folder to more serious file-system exposure if the target handler performs additional operations (create/write) at that path.

### Likelihood Explanation
Likelihood is low-to-moderate and requires the victim to open a repository containing a crafted submodule/gitlink entry and to click into that specific diff, then explicitly click "open" on the submodule diff (a required user action similar to other Desktop repo-content-triggered issues). No admin rights, prior compromise, or credential leakage is needed — only cloning/opening a malicious repository, which fits the "attacker controls a cloned/fetched repository" threat model.

### Recommendation
- In `buildSubmoduleDiff` (`app/src/lib/git/diff.ts`), resolve `fullPath` with `path.resolve` and verify (e.g. via `path.relative`) that the resolved path is still contained within `repository.path` before returning it in the `ISubmoduleDiff` object.
- Reject/sanitize submodule paths containing `..` segments or absolute-path indicators at the point they are parsed out of `git status`/`git diff` output.
- Apply the same containment check wherever `onOpenSubmodule`/`fullPath` is consumed, as defense in depth.

### Proof of Concept
Conceptual (not fully verified against a live build, see caveats):
1. Craft a malicious repository whose recorded submodule/gitlink entry path contains traversal segments (e.g. `../../evil`) so that `git status`/`git diff` reports this as the submodule's `path`.
2. Victim clones/opens the repository in GitHub Desktop and navigates to the Changes/History view showing this submodule as changed.
3. `getWorkingDirectoryDiff` → `buildDiff` → `buildSubmoduleDiff` computes `fullPath = Path.join(repository.path, '../../evil')`, which resolves outside the repository.
4. The `SubmoduleDiff` component renders an "Open submodule" action wired to `onOpenSubmoduleClick`, which calls `onOpenSubmodule(fullPath)` with the traversal-escaped path.

### Caveats
I could not fully trace the terminal consumer(s) of `onOpenSubmodule` (e.g. `repository.tsx`, `changes.tsx`, `selected-commits.tsx` all reference it) within the indexed context to confirm exactly what filesystem operation is performed on the resulting path (open dialog vs. shell/explorer launch vs. something more consequential). Because of index size limits, I was not able to retrieve the full body of those handler functions. If precise confirmation of the downstream sink and its side effects is needed, a full Devin session with direct repository access would be required to trace `onOpenSubmodule` end-to-end and confirm exploitability/severity.

### Citations

**File:** app/src/lib/git/diff.ts (L798-806)
```typescript
async function buildSubmoduleDiff(
  buffer: Buffer,
  repository: Repository,
  file: FileChange,
  status: SubmoduleStatus
): Promise<IDiff> {
  const path = file.path
  const fullPath = Path.join(repository.path, path)
  const url = await getConfigValue(repository, `submodule.${path}.url`, true)
```

**File:** app/src/lib/git/status.ts (L297-349)
```typescript
function buildStatusMap(
  files: Map<string, WorkingDirectoryFileChange>,
  entry: IStatusEntry,
  conflictDetails: ConflictFilesDetails
): Map<string, WorkingDirectoryFileChange> {
  const status = mapStatus(
    entry.statusCode,
    entry.submoduleStatusCode,
    entry.renameOrCopyScore
  )

  if (status.kind === 'ordinary') {
    // when a file is added in the index but then removed in the working
    // directory, the file won't be part of the commit, so we can skip
    // displaying this entry in the changes list
    if (
      status.index === GitStatusEntry.Added &&
      status.workingTree === GitStatusEntry.Deleted
    ) {
      return files
    }
  }

  if (status.kind === 'untracked') {
    // when a delete has been staged, but an untracked file exists with the
    // same path, we should ensure that we only draw one entry in the
    // changes list - see if an entry already exists for this path and
    // remove it if found
    files.delete(entry.path)
  }

  // for now we just poke at the existing summary
  const appStatus = convertToAppStatus(
    entry.path,
    status,
    conflictDetails,
    entry.oldPath
  )

  const initialSelectionType =
    appStatus.kind === AppFileStatusKind.Modified &&
    appStatus.submoduleStatus !== undefined &&
    !appStatus.submoduleStatus.commitChanged
      ? DiffSelectionType.None
      : DiffSelectionType.All

  const selection = DiffSelection.fromInitialSelection(initialSelectionType)

  files.set(
    entry.path,
    new WorkingDirectoryFileChange(entry.path, appStatus, selection)
  )
  return files
```

**File:** app/src/ui/diff/submodule-diff.tsx (L209-211)
```typescript
  private onOpenSubmoduleClick = () => {
    this.props.onOpenSubmodule?.(this.props.diff.fullPath)
  }
```
