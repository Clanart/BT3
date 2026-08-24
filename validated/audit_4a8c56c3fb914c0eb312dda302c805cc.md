## Title
Symlink-based path traversal via `Path.join(repository.path, file.path)` bypasses repo-boundary checks in file-reveal/open actions - ([File: app/src/lib/app-shell.ts])

## Summary
GitHub Desktop has a hardened helper, `resolveWithin()`, that resolves a repo-relative path and rejects it (returns `null`) if the real, symlink-resolved path escapes the repository root [1](#0-0) . This helper is correctly used for the deep-link `filepath` action [2](#0-1)  and for the Copilot conflict-resolution file reader [3](#0-2) . However, several other user-facing "Reveal in Finder / Explorer" and "Open in external editor / default program" code paths still build the on-disk path with plain `Path.join(repository.path, path)` and pass it straight to Electron shell APIs, with no symlink/real-path check.

## Finding Description
`revealInFileManager()` in `app-shell.ts` joins the repository root with a caller-supplied relative path and calls `shell.showItemInFolder()` without any containment check: [4](#0-3) 

The same unguarded `Path.join(repository.path, path)` pattern is used to build `fullPath` before calling `openInExternalEditor` / `onOpenFile` / `openFile` (which calls `shell.openExternal('file://...')`) in:
- `app/src/ui/history/selected-commits.tsx` (context menu for a selected commit's changed files) [5](#0-4) 
- `app/src/ui/open-pull-request/pull-request-files-changed.tsx` (context menu for files changed in a pull request, populated from GitHub API/diff data for a PR that can originate from a fork) [6](#0-5) 
- `app/src/ui/changes/filter-changes-list.tsx` (Changes list context menu) [7](#0-6) 
- `app/src/ui/app.tsx` `onOpenInExternalEditor` [8](#0-7) 
- `app/src/ui/lib/conflicts/unmerged-file.tsx` and `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx` (merge-conflict "Open with default program" / "Reveal in Finder") [9](#0-8) [10](#0-9) 

Git allows a tracked blob to be a symlink (mode `120000`) whose target text is entirely attacker controlled at commit time. A malicious repository the user clones or fetches (or a malicious fork whose PR is browsed via "Open pull request in Desktop") can therefore contain, e.g., `evil-dir -> ../../../../` or an absolute path such as `/Users/victim/.ssh`, checked out as a real OS symlink inside the working tree. When the user later right-clicks any path that traverses through that symlinked directory in Changes, History, or the pull-request diff viewer and chooses "Reveal in Finder/Explorer," "Open in external editor," or "Open with default program," Desktop computes the path with plain `Path.join()` and hands it to `shell.showItemInFolder`, `launchExternalEditor`, or `shell.openExternal('file://...')`. None of these call sites reject the case where the joined path's realpath resolves outside `repository.path` — unlike `resolveWithin()`, which explicitly guards exactly this scenario (see the passing "fails for paths that use a symlink to traverse outside of the root" unit test) [11](#0-10) .

The `isSafeFileExtension` check gates only the "Open with default program" menu item and only inspects the extension of the git-tracked path string, not the resolved target — it does not defend against the symlink escaping the repo boundary at all, and `RevealInFileManagerLabel` / `openInExternalEditor` items are not extension-gated in the first place [12](#0-11) .

## Impact Explanation
This lets an attacker-controlled cloned/fetched repository (or a malicious fork whose PR is loaded via "Open pull request in Desktop", an unprivileged, no-write-access action) cause the victim's own Desktop UI actions to read or reveal files outside the repository — e.g., silently opening `~/.ssh/id_rsa`, browser cookie stores, or other sensitive files in the user's default text editor/program, or revealing a sensitive system directory in Finder/Explorer. Because `openFile()`/`openInExternalEditor()` hand the resolved OS path to the platform's file-open machinery, the practical outcome is credential/file disclosure and can additionally act as a stepping stone to further exploitation depending on the default handler associated with the target file's real extension (which the attacker also partially controls, since the display name shown to the user is the git-tracked symlink name, but the actual bytes opened are whatever the symlink target contains).

## Likelihood Explanation
The victim only needs to perform ordinary Desktop workflows — clone/fetch an untrusted repository, browse History/Changes, or open a PR from an untrusted fork — and then use a standard context-menu action ("Reveal in Finder," "Open in external editor," "Open with default program") on a file that happens to live under an attacker-planted symlinked directory. No admin rights, local access, or unnatural steps are required beyond normal repository browsing, and Desktop already treats this exact primitive (symlink-based repo escape) as a real threat elsewhere in the codebase (`resolveWithin`), confirming it is a recognized, reachable attack class that simply wasn't applied consistently to every file-open/reveal code path.

## Recommendation
Route every `Path.join(repository.path, path)` construction that feeds `shell.showItemInFolder`, `launchExternalEditor`/`launchCustomExternalEditor`, `openFile`/`shell.openExternal`, and `shell.openPath` through `resolveWithin()` (or an equivalent realpath-based containment check), refusing the action and surfacing an error when the resolved path escapes the repository root — mirroring what `dispatcher.ts`'s `openRepositoryFromUrl` and `copilot-conflict-context.ts` already do. Apply this consistently to `app-shell.ts#revealInFileManager`, `selected-commits.tsx`, `filter-changes-list.tsx`, `pull-request-files-changed.tsx`, `app.tsx#onOpenInExternalEditor`, `unmerged-file.tsx`, and `copilot-conflicts-dialog.tsx`.

## Proof of Concept
1. Attacker creates a public repository containing a tracked symlink entry `escape` (mode `120000`) whose blob content is `../../../../../../../../Users/victim/.ssh` (or an absolute path), plus a regular file `escape/id_rsa` that is really the traversed path.
2. Victim clones this repository with GitHub Desktop, or the attacker opens a pull request from a fork containing this symlink against a repo the victim has open, and the victim uses "Open pull request in Desktop."
3. In the Changes list / History view / PR "Files changed" view, the victim right-clicks the entry corresponding to `escape/id_rsa` and selects "Reveal in Finder" (`app/src/lib/app-shell.ts:61-64`) or "Open with default program" (`app/src/ui/lib/open-file.ts`).
4. Because `Path.join(repository.path, 'escape/id_rsa')` is not checked with `resolveWithin`, the OS resolves the symlink and Finder/Explorer opens (or the default text editor displays) `~/.ssh/id_rsa` outside the cloned repository, disclosing its contents to the attacker-influenced UI flow.

Note: I was not able to execute this end-to-end in a live Desktop build (no filesystem/terminal access in this mode), so the PoC is derived from static code-path analysis of the cited files rather than a verified runtime reproduction — a background Devin session with repo checkout and a sandbox would be needed to fully validate the exploit chain.

### Citations

**File:** app/src/lib/path.ts (L36-71)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1971)
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

**File:** app/src/ui/history/selected-commits.tsx (L371-420)
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
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenItem(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
      },
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L154-199)
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
      },
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenFile(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
```

**File:** app/src/ui/changes/filter-changes-list.tsx (L638-654)
```typescript
  private getOpenInExternalEditorMenuItem = (
    file: WorkingDirectoryFileChange,
    enabled: boolean
  ): IMenuItem => {
    const { externalEditorLabel } = this.props

    const openInExternalEditor = externalEditorLabel
      ? `Open in ${externalEditorLabel}`
      : DefaultEditorLabel

    return {
      label: openInExternalEditor,
      action: () => {
        this.props.onOpenItemInExternalEditor(file.path)
      },
      enabled,
    }
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
