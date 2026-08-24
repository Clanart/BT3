## Finding [1](#0-0) 

The bug-class in the Solidity report is: a "trusted"/special creation path (`createMasterAccount`) skips a validation step (`referrer != address(0)`) that later code unconditionally assumes has already happened, breaking the invariant for that class of accounts. The GitHub Desktop analog is a validation step (path-traversal containment via `resolveWithin`) that was added for **one** call site handling attacker-influenced file paths, while several **sibling** call sites that consume the exact same kind of attacker-influenced data skip it and fall back to a raw `Path.join`.

### Title
Path traversal via crafted file path in PR/diff/commit views bypasses the app's own `resolveWithin` containment guard - (File: `app/src/lib/app-shell.ts`)

### Summary
`app/src/lib/dispatcher/dispatcher.ts`'s `openRepositoryFromUrl` explicitly hardens against a malicious `filepath` from a deep link by rejecting absolute paths and calling `resolveWithin(repository.path, filepath)` before ever touching the filesystem: [2](#0-1) 

This shows the team is aware that a file path string coming from outside the app (in this case a deep-link query parameter) must be validated to stay inside the repository root before being handed to `shell.showItemInFolder`.

However, `revealInFileManager` — the shared helper used from many other surfaces (working directory changes, committed file diffs, PR "Files Changed" view, stash diffs, merge-conflict resolution menus) — does **not** apply the same guard: [1](#0-0) 

```ts
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
}
```

The `path` argument here is `file.path`, sourced from git status/diff parsing of commit content that is not necessarily under the user's control — e.g. the "Open Pull Request" / "Files Changed" preview reads the diff of a fork's `head` commit that has been fetched but is deliberately **not checked out** (see `nonLocalCommitSHA` handling in `pull-request-files-changed.tsx` and `selected-commits.tsx`): [3](#0-2) [4](#0-3) 

Because this is a diff/preview of an un-checked-out commit, git's own checkout-time path-safety guard (`unpack-trees`'s refusal to materialize entries with `..` components) is never invoked — that guard only runs when git actually writes files to the worktree. The `revealInFileManager`/`onOpenInExternalEditor` code paths in Desktop then trust that string directly with a bare `Path.join`, unlike the one place the app already learned to distrust it.

The same unguarded `Path.join(repository.path, path)` pattern recurs at: [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

### Finding Description
The broken invariant is: "any file path taken from repository content that is displayed to the user has already been sanitized to remain inside the repository root." That invariant is explicitly enforced only for the `filepath` query parameter of the `x-github-client://openRepo` deep link (via `isAbsolute()` + `resolveWithin()` in `dispatcher.ts`), but is silently assumed — and not enforced — for `file.path` values that flow out of diff/status parsing for content belonging to a remote, attacker-controlled fork that a user is merely previewing (not merged, not checked out). `revealInFileManager` and the several UI call sites above join that string onto `repository.path` with plain `Path.join`, which does not strip `..` segments or resolve symlinks/traversal, and none of them re-run `resolveWithin` before invoking `shell.showItemInFolder` / `shell.openPath` / `openFile`.

### Impact Explanation
If a crafted fork/PR head commit tree contains an entry whose path can be represented with traversal segments once serialized through Desktop's diff/status parsing (e.g. via low-level pack manipulation that bypasses git's own client-side tree-name validation, which is only enforced at checkout time, not at diff time), a victim who merely opens "Preview Pull Request" and right-clicks → "Reveal in Finder/Explorer" or "Open in external editor" on that entry can be made to open/reveal a file located outside the repository working directory. Since the checked-out-tree safety net never runs (the commit is not checked out), Desktop's own `Path.join`-based helpers are the only remaining defense, and they don't validate containment the way `resolveWithin` does elsewhere in the same codebase.

### Likelihood Explanation
Requires an attacker to control a fork/PR (or any git object graph fetched into the local repository, e.g. via a malicious remote) and get the victim to preview its diff and interact with a specific context-menu action on the crafted entry — no local access, admin rights, or pre-existing malware needed, matching the "attacker controls a cloned/fetched repository or GitHub API object" criterion. The likelihood is bounded by whether a raw path-traversal tree entry can survive into Desktop's diff parsing without being rejected earlier (by git's own object validation) — this is the detail that would need confirmation against the specific dugite/git version bundled, since standard porcelain commands like `git diff --name-status` generally also reject such entries. Given the codebase authors themselves added a `resolveWithin` guard specifically for file paths derived from external/less-trusted sources, this suggests they considered such a scenario plausible enough to defend against in at least one place.

### Recommendation
Apply the same containment check used in `dispatcher.ts`'s `openRepositoryFromUrl` (reject absolute paths, then `resolveWithin(repository.path, path)`) inside `revealInFileManager` and any other helper (`onOpenInExternalEditor`, `openFile` invocations from diff/PR context menus, `copilot-conflicts-dialog.tsx`, `unmerged-file.tsx`) that joins a file path sourced from parsed git content — especially content belonging to commits that have not been checked out — before passing it to `shell.showItemInFolder`, `shell.openPath`, or `openFile`.

### Proof of Concept
1. Attacker publishes a fork/branch whose head commit's tree contains a crafted entry that Desktop's diff/status parser will surface with a path such as `../../../../.ssh/authorized_keys` (feasible only if the diff-parsing path does not itself reject `..` components, since it operates on raw diff/tree data rather than a checked-out worktree).
2. Victim opens "Preview Pull Request" for that branch in Desktop; `pull-request-files-changed.tsx` renders the crafted `CommittedFileChange.path` in the Files list without the commit ever being checked out.
3. Victim right-clicks the entry and selects "Reveal in Finder/Explorer" or "Open in external editor".
4. `revealInFileManager(repository, file.path)` / `dispatcher.openInExternalEditor(Path.join(repository.path, path))` resolve to a path outside the repository, and the OS shell opens/reveals that external file — with no `resolveWithin` check ever executed, unlike the equivalent `filepath` handling in `openRepositoryFromUrl`.

### Citations

**File:** app/src/lib/app-shell.ts (L55-64)
```typescript
/**
 * Reveals a file from a repository in the native file manager.
 *
 * @param repository The currently active repository instance
 * @param path The path of the file relative to the root of the repository
 */
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
}
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L154-194)
```typescript
  private onFileContextMenu = async (
    file: CommittedFileChange,
    event: React.MouseEvent<HTMLDivElement>
  ) => {
    event.preventDefault()

    const { repository } = this.props

    const fullPath = Path.join(repository.path, file.path)
    const fileExistsOnDisk = await pathExists(fullPath)
    if (!fileExistsOnDisk) {
      showContextualMenu([
        {
          label: __DARWIN__
            ? 'File Does Not Exist on Disk'
            : 'File does not exist on disk',
          enabled: false,
        },
      ])
      return
    }

    const { externalEditorLabel, dispatcher } = this.props

    const extension = Path.extname(file.path)
    const isSafeExtension = isSafeFileExtension(extension)
    const openInExternalEditor =
      externalEditorLabel !== undefined
        ? `Open in ${externalEditorLabel}`
        : DefaultEditorLabel

    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
      {
        label: openInExternalEditor,
        action: () => dispatcher.openInExternalEditor(fullPath),
        enabled: fileExistsOnDisk,
```

**File:** app/src/ui/history/selected-commits.tsx (L371-415)
```typescript
  private onContextMenu = async (
    file: CommittedFileChange,
    event: React.MouseEvent<HTMLDivElement>
  ) => {
    event.preventDefault()

    const {
      selectedCommits,
      localCommitSHAs,
      repository,
      externalEditorLabel,
    } = this.props

    const fullPath = Path.join(repository.path, file.path)
    const fileExistsOnDisk = await pathExists(fullPath)
    if (!fileExistsOnDisk) {
      showContextualMenu([
        {
          label: __DARWIN__
            ? 'File Does Not Exist on Disk'
            : 'File does not exist on disk',
          enabled: false,
        },
      ])
      return
    }

    const extension = Path.extname(file.path)

    const isSafeExtension = isSafeFileExtension(extension)
    const openInExternalEditor = externalEditorLabel
      ? `Open in ${externalEditorLabel}`
      : DefaultEditorLabel

    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
      {
        label: openInExternalEditor,
        action: () => this.props.onOpenInExternalEditor(file.path),
        enabled: fileExistsOnDisk,
      },
```

**File:** app/src/ui/app.tsx (L3429-3437)
```typescript
  private onOpenInExternalEditor = (path: string) => {
    const repository = this.state.selectedState?.repository
    if (repository === undefined) {
      return
    }

    const fullPath = Path.join(repository.path, path)
    this.props.dispatcher.openInExternalEditor(fullPath)
  }
```

**File:** app/src/ui/changes/filter-changes-list.tsx (L628-636)
```typescript
  private getRevealInFileManagerMenuItem = (
    file: WorkingDirectoryFileChange
  ): IMenuItem => {
    return {
      label: RevealInFileManagerLabel,
      action: () => revealInFileManager(this.props.repository, file.path),
      enabled: file.status.kind !== AppFileStatusKind.Deleted,
    }
  }
```

**File:** app/src/ui/lib/conflicts/unmerged-file.tsx (L396-418)
```typescript
/** makes a click handling function for marker conflict actions */
const makeMarkerConflictDropdownClickHandler = (
  relativeFilePath: string,
  repository: Repository,
  dispatcher: Dispatcher,
  status: ConflictsWithMarkers,
  ourBranch: string | undefined,
  theirBranch: string | undefined,
  setIsFileResolutionOptionsMenuOpen: (
    isFileResolutionOptionsMenuOpen: boolean
  ) => void
) => {
  return () => {
    const absoluteFilePath = join(repository.path, relativeFilePath)
    const items: IMenuItem[] = [
      {
        label: OpenWithDefaultProgramLabel,
        action: () => openFile(absoluteFilePath, dispatcher),
      },
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, relativeFilePath),
      },
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L212-234)
```typescript
  private onOverflowMenuClick = (path: string) => {
    const { repository, dispatcher, resolvedExternalEditor } = this.props
    const absolutePath = join(repository.path, path)

    const items: IMenuItem[] = []

    if (resolvedExternalEditor !== null) {
      items.push({
        label: `Open in ${resolvedExternalEditor}`,
        action: () => this.props.openFileInExternalEditor(absolutePath),
      })
    }

    items.push(
      {
        label: OpenWithDefaultProgramLabel,
        action: () => openFile(absolutePath, dispatcher),
      },
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, path),
      }
    )
```
