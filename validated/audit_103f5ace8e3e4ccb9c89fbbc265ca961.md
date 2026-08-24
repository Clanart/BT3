## Title
Missing symlink/traversal guard when opening working-directory files from a cloned/checked-out repo lets a malicious commit redirect "Open in External Editor" / "Reveal in File Manager" outside the repo - (File: `app/src/ui/changes/sidebar.tsx`, `app/src/ui/open-pull-request/pull-request-files-changed.tsx`, `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx`)

### Summary
The Plonky3/SP1 report's common thread is: a value that is nominally validated in one code path (root proof `vk_root` check, `MAX_MEMORY` bound check) is *not* validated in a structurally similar path that shares the same trust boundary, letting attacker-controlled input slip through. Desktop has the same pattern for file paths derived from a cloned/fetched repository or PR diff: `app/src/lib/copilot-conflict-context.ts` and `app/src/ui/dispatcher/dispatcher.ts`'s `openRepositoryFromUrl` both call `resolveWithin()` (which resolves symlinks via `realpath` and rejects results that escape `rootPath`) before touching disk. But the much more commonly used "open this changed/committed file" UI actions — in `app/src/ui/changes/sidebar.tsx`, `app/src/ui/changes/filter-changes-list.tsx`, `app/src/ui/open-pull-request/pull-request-files-changed.tsx`, `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx`, and `app/src/ui/lib/conflicts/unmerged-file.tsx` — build the target path with a plain `Path.join(repository.path, path)`/`join(repository.path, path)` and pass it straight to `openFile`, `launchExternalEditor`/`launchCustomExternalEditor`, or `revealInFileManager`, with no traversal/symlink containment check.

### Finding Description
`file.path` values displayed in the Changes list, PR "Files Changed" view, and Copilot conflict dialog originate from git tree/status entries in a repository the attacker fully controls (a cloned repo, a fork's PR, or a rebase/merge source). Git blocks literal `..` path *components* in tree entries, but it does not stop a tracked path from itself being a **symlink** whose target resolves outside the working directory (e.g. a committed symlink `evil-file.txt -> /Users/victim/.ssh/id_rsa` or `..\..\..\..\Windows\System32\...`).

Compare the two code paths:

- Guarded (added specifically to close this exact class of bug): [1](#0-0) 
which explicitly comments "Guard against path traversal and symlink escapes (cross-platform)" and rejects the resolved path if it is outside the repository.

- Unguarded — `sidebar.tsx` builds the open target with a bare `Path.join` and hands it directly to `openFile`, which calls `shell.openExternal('file://' + fullPath)`: [2](#0-1) [3](#0-2) 

- Unguarded — the PR "Files Changed" context menu resolves `fullPath` the same way and offers "Open in External Editor" and "Reveal in File Manager" without a containment check (only an `isSafeExtension` gate that applies to "Open With Default Program", not to the editor/reveal actions): [4](#0-3) 

- Unguarded — the Copilot conflict resolution overflow menu does the same `join(repository.path, path)` before opening in editor/default program/file manager: [5](#0-4) 

- Unguarded — `unmerged-file.tsx` similarly joins the repository path with the conflicted file path before invoking the external editor: [6](#0-5) 

The safety primitive that exists — `resolveWithin()` in `app/src/lib/path.ts`, which calls `realpath` on both the root and the resolved path and verifies the resolved real path still starts with the real root — is only wired into `copilot-conflict-context.ts` and `dispatcher.ts`'s deep-link `filepath` handling: [7](#0-6) [8](#0-7) 

None of the other "open file" call sites reuse it, so the exact guard shown to be necessary in one part of the code is absent from sibling paths that consume the same attacker-controlled input class (a tracked repo path).

### Impact Explanation
This allows an unprivileged attacker who controls a cloned/fetched repository (or a PR opened against the victim's repo) to commit a symlink under a benign-looking name. When the victim later, in the ordinary course of using Desktop, chooses "Open in External Editor" or "Reveal in File Manager" on that entry (from the Changes list, a PR's Files Changed tab, or a merge/rebase conflict dialog), Desktop resolves and opens the symlink target — a file **outside the repository root**, potentially a sensitive file (SSH keys, config files, `.git-credentials`, other users' files) or, via "Open With Default Program"/`shell.openExternal('file://...')`, a file whose OS file-association triggers execution (subject to `isSafeFileExtension`, which is only applied to some of these menu entries and not others, e.g. not to "Open in External Editor"). This is unprompted disclosure/opening of out-of-repo content triggered by a normal, expected user action — matching the report's "attacker controls a cloned/fetched repository ... result is file read outside the repo".

### Likelihood Explanation
Medium-high: opening a changed/conflicted file via the context menu or the PR review UI is a routine action Desktop users take constantly; no unusual steps are required beyond opening a PR or repo that contains a malicious symlink and clicking a standard menu item. The attack requires no admin rights, no local access, and no social engineering beyond the repository/PR itself being opened — squarely within the disclosed "valid impact" scope.

### Recommendation
Route every "open/reveal file from repository content" call site through `resolveWithin()` (or an equivalent realpath-based containment check) before calling `openFile`, `launchExternalEditor`, `launchCustomExternalEditor`, or `revealInFileManager`, mirroring the guard already implemented in `app/src/lib/copilot-conflict-context.ts` and `dispatcher.ts`. Reject or warn on paths that resolve outside `repository.path`, and apply `isSafeFileExtension`-style gating consistently across all "open" actions, not just "Open With Default Program".

### Proof of Concept
1. Attacker creates a repo (or opens a PR against the victim's fork) containing a symlink, e.g. `git symlink-add.sh`: `ln -s /Users/victim/.ssh/id_rsa leak.txt && git add leak.txt && git commit`.
2. Victim clones/fetches the repo or opens the PR in Desktop; `leak.txt` shows up as a tracked file/changed file.
3. Victim right-clicks `leak.txt` in the Changes list (or PR Files Changed view) and selects "Open in External Editor" (or "Reveal in File Manager").
4. `sidebar.tsx`'s `onOpenItem`/`pull-request-files-changed.tsx`'s context-menu handler computes `Path.join(repository.path, "leak.txt")`, which — because the OS resolves the symlink at open time — actually opens `/Users/victim/.ssh/id_rsa` in the configured editor or reveals it in Explorer/Finder, with no `resolveWithin` check to intercept the escape, unlike the equivalent, already-hardened path in `copilot-conflict-context.ts`.

### Citations

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

**File:** app/src/ui/changes/sidebar.tsx (L277-285)
```typescript
  /**
   * Open file with default application.
   *
   * @param path The path of the file relative to the root of the repository
   */
  private onOpenItem = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/lib/open-file.ts (L1-17)
```typescript
import { shell } from '../../lib/app-shell'
import { Dispatcher } from '../dispatcher'

export async function openFile(
  fullPath: string,
  dispatcher: Dispatcher
): Promise<void> {
  const result = await shell.openExternal(`file://${fullPath}`)

  if (!result) {
    const error = {
      name: 'no-external-program',
      message: `Unable to open file ${fullPath} in an external program. Please check you have a program associated with this file extension`,
    }
    await dispatcher.postError(error)
  }
}
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L154-200)
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

**File:** app/src/ui/lib/conflicts/unmerged-file.tsx (L90-94)
```typescript
      path: props.path,
      status: props.status,
      resolvedExternalEditor: props.resolvedExternalEditor,
      onOpenEditorClick: () =>
        props.openFileInExternalEditor(join(props.repository.path, props.path)),
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
