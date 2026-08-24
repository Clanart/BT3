## Title
File-open actions in the Changes/History/PR views bypass `resolveWithin` symlink/path-traversal guard, allowing a malicious repository to redirect "Open"/"Reveal" actions outside the repo - (File: `app/src/lib/app-shell.ts`, `app/src/ui/changes/sidebar.tsx`, `app/src/ui/history/selected-commits.tsx`, `app/src/ui/open-pull-request/pull-request-files-changed.tsx`)

### Summary
GitHub Desktop has a purpose-built guard, `resolveWithin` (`app/src/lib/path.ts`), that resolves a repository-relative path and rejects it if it escapes the repository root — including via symlinks, which the accompanying unit tests explicitly exercise. [1](#0-0) [2](#0-1)  This guard is correctly wired into a small number of call sites (the `x-github-client://openRepo` deep-link `filepath` handler and the Copilot conflict-context file reader), [3](#0-2) [4](#0-3)  but the much more commonly used "Open with default program" / "Reveal in Finder"/"Open in external editor" actions in the Changes list, commit/PR file lists, and merge-conflict dialogs instead build the target path with a plain `Path.join(repository.path, path)`, with no containment check at all.

### Finding Description
`revealInFileManager` in `app/src/lib/app-shell.ts` takes an attacker-influenced repository-relative `path` and does:
```
const fullyQualifiedFilePath = Path.join(repository.path, path)
return shell.showItemInFolder(fullyQualifiedFilePath)
``` [5](#0-4) 

Its own interface documents "Do not use this method with non-validated paths," yet `path` here is exactly a repository-relative path taken straight from git status/diff/PR file data. [6](#0-5) 

The same unguarded `Path.join(repository.path, path)` pattern is repeated at every major "open a changed/committed file" entry point:
- Changes sidebar "Open with default program": [7](#0-6) 
- Commit/history file list "Open with default program" and its context menu (`RevealInFileManagerLabel`, `openInExternalEditor`, `OpenWithDefaultProgramLabel`): [8](#0-7) [9](#0-8) 
- Pull request files-changed view/context menu (path comes straight from PR diff data, i.e., a GitHub API object an attacker controls by opening a PR): [10](#0-9) [11](#0-10) 
- Merge/rebase conflict resolution menus: [12](#0-11) [13](#0-12) 
- Binary file / diff image viewers: [14](#0-13) [15](#0-14) 

The only gate applied before these actions in several of them is `pathExists`/`fs.stat`, which merely checks that *something* exists at the resolved (symlink-following) location — it does not confirm the resolved path stays inside the repository. [16](#0-15) 

**Broken invariant:** "a file path drawn from repository content (working tree status, a commit, or a pull request diff) must stay within the repository root before it is used for a filesystem/shell operation." The team already built and unit-tested a function for exactly this (`resolveWithin`, with an explicit symlink-escape test case), but applied it only to the deep-link `filepath` action and the Copilot conflict reader — every other file-open code path performs the equivalent operation via a bare `Path.join` and therefore inherits none of that protection.

**Attacker primitive:** a git tree can contain a symlink blob (mode `120000`) whose target string is arbitrary, e.g. `evil -> ../../../../Library/Application Support` or an absolute path. Git will faithfully recreate this symlink in the working directory on clone/fetch/checkout/PR-branch-checkout with no path validation of its own. Once the symlink exists on disk under the repo, any of the "Open"/"Reveal" actions above joins `repository.path` with a tracked path that traverses through it, and the OS resolves the symlink at open time — landing outside the repository.

### Impact Explanation
A user who clones or fetches a malicious repository, or simply reviews/opens a pull request from an untrusted fork inside GitHub Desktop, can be induced (via a single click on "Open with default program" / "Reveal in Finder" / double-click in the Changes or PR file list — all normal, expected Desktop workflows) to have the application operate on a file path outside the repository that the attacker chose. Depending on the symlink target and OS handling this can mean: revealing/exfiltrating sensitive directories (e.g. `~/.ssh`, credential stores) in Finder/Explorer, or launching an arbitrary file with the OS default handler (`shell.openExternal('file://' + fullPath)` in `app/src/ui/lib/open-file.ts`) [17](#0-16)  which can execute scripts/applications if the symlink points at an executable the OS is willing to auto-run for that extension. This satisfies the in-scope impact class "attacker controls a cloned/fetched repository … and the result is code execution, file write or read outside the repo."

### Likelihood Explanation
High. No local access, admin rights, or social engineering beyond normal product usage is required — cloning a repo or opening a PR and clicking a standard context-menu item is the intended user flow. The vulnerable pattern is duplicated across many independent UI components, so it's reachable from Changes, History, and Open-Pull-Request views alike, and it is the *majority* code path (the validated `resolveWithin` path is the exception, used in only two places).

### Recommendation
Route every repository-relative file path used for `shell.showItemInFolder`, `shell.openExternal('file://...')`, `openFileInExternalEditor`, and direct `readFile` calls (diff image loading) through `resolveWithin(repository.path, path)` before use, mirroring what `dispatcher.ts`'s `openRepositoryFromUrl` and `copilot-conflict-context.ts` already do, and refuse the operation (as those two call sites do) when the resolved path is `null`. Centralize this in a single helper (e.g., extend `revealInFileManager`/`openFile` themselves to perform the check) so future call sites cannot regress by reintroducing bare `Path.join`.

### Proof of Concept
1. Attacker creates a repository containing a symlink entry (mode `120000`) named e.g. `notes.txt` whose target is `../../../../../../Users/victim/.ssh` (or any sensitive/executable path reachable via relative traversal from the eventual clone location).
2. Victim clones this repository (or the attacker opens a PR containing this symlinked file against a repo the victim reviews) in GitHub Desktop; git checks out the symlink into the working tree without any Desktop-side validation.
3. Victim opens the Changes tab (or the PR's "Files changed" tab), right-clicks `notes.txt`, and selects "Reveal in Finder" / "Open with default program".
4. `revealInFileManager`/`onOpenItem`/`onFileContextMenu` compute `Path.join(repository.path, 'notes.txt')`, which the OS resolves through the symlink, showing/opening the attacker-chosen target location instead of anything inside the repository — with no `resolveWithin` check ever executed, unlike the deep-link `filepath` flow which would have rejected this exact traversal.

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

**File:** app/src/lib/app-shell.ts (L16-40)
```typescript
  /**
   * Reveals the specified file using the operating
   * system default application.
   * Do not use this method with non-validated paths.
   *
   * @param path - The path of the file to open
   */

  readonly openPath: (path: string) => Promise<string>
  /**
   * Reveals the specified file on the operating system
   * default file explorer. If a folder is passed, it will
   * open its parent folder and preselect the passed folder.
   *
   * @param path - The path of the file to show
   */
  readonly showItemInFolder: (path: string) => void
  /**
   * Reveals the specified folder on the operating
   * system default file explorer.
   * Do not use this method with non-validated paths.
   *
   * @param path - The path of the folder to open
   */
  readonly showFolderContents: (path: string) => void
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

**File:** app/src/ui/history/selected-commits.tsx (L287-295)
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L86-89)
```typescript
  private onOpenFile = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    this.onOpenBinaryFile(fullPath)
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

**File:** app/src/ui/diff/binary-file.tsx (L18-23)
```typescript
/** represents the default view for a file that we cannot render a diff for */
export class BinaryFile extends React.Component<IBinaryFileProps, {}> {
  private open = () => {
    const fullPath = Path.join(this.props.repository.path, this.props.path)
    this.props.onOpenBinaryFile(fullPath)
  }
```

**File:** app/src/lib/git/diff.ts (L926-937)
```typescript
export async function getWorkingDirectoryImage(
  repository: Repository,
  file: FileChange
): Promise<Image> {
  const contents = await readFile(Path.join(repository.path, file.path))
  return new Image(
    contents.buffer,
    contents.toString('base64'),
    getMediaType(Path.extname(file.path)),
    contents.length
  )
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
