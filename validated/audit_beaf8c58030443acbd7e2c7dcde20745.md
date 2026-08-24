### Title
`revealInFileManager` and `openFile` join committed-file paths onto the repository root without the `resolveWithin` traversal guard used elsewhere in Desktop - ([File: app/src/lib/app-shell.ts])

### Summary
GitHub Desktop has a dedicated path-traversal guard, `resolveWithin` (`app/src/lib/path.ts`), that resolves a segment against a root and returns `null` if the result escapes the root. [1](#0-0)  It is used in `dispatcher.ts` specifically to defend against a `filepath` supplied via an `x-github-client://` deep link before it is passed to `shell.showItemInFolder`. [2](#0-1)  However, several other call sites that build a full filesystem path from a **file path taken from a commit/diff/PR file list** — data that ultimately originates from a cloned/fetched repository or the GitHub API — use plain `Path.join(repository.path, path)` with no such check before invoking `shell.showItemInFolder` or opening the file with the OS default handler.

### Finding Description
`revealInFileManager` builds the path to reveal with an unguarded `Path.join` and immediately calls `shell.showItemInFolder`: [3](#0-2) 

Its own `IAppShell.showItemInFolder`/`openPath` doc comments explicitly warn "Do not use this method with non-validated paths," acknowledging the need for validation. [4](#0-3) 

This function is called with `file.path` values coming directly from committed-file lists in multiple UI components, e.g. the commit history view (`selected-commits.tsx`): [5](#0-4) 

and the pull-request "Files Changed" view, which sources its file list from PR diff data (attacker-controlled if the attacker opens/updates a PR the user views) and joins it directly for `openFile`/`revealInFileManager`: [6](#0-5) [7](#0-6) 

and again for conflict resolution in the multi-commit-operation flow: [8](#0-7) 

The invariant broken here is the same class as the smart-contract report: a security guard (`resolveWithin`/traversal check) exists in the codebase and is correctly applied on one path-construction call site (`openRepositoryFromUrl`) but is inconsistently omitted on sibling call sites that construct paths from equally untrusted input (git tree/diff file paths, PR file paths) before passing them to filesystem-affecting APIs (`shell.showItemInFolder`, `openFile`). `Path.join` alone does **not** prevent `..` segments from escaping the root, unlike `resolveWithin`, which explicitly normalizes, resolves, and verifies the real path stays under the real root. [9](#0-8) 

### Impact Explanation
If a `file.path` (or `oldPath` for renames — see `getOldPathOrDefault`, which returns the raw path with no sanitization) [10](#0-9)  could contain traversal segments (e.g., via a maliciously crafted commit in a fetched/cloned repository, or a PR files-changed entry from the GitHub API with an unexpected path), the resulting `fullPath`/`fullyQualifiedFilePath` could resolve outside the repository working directory. This would let `revealInFileManager` or `openFile` operate on (reveal/open with the OS default handler) an arbitrary file on the user's disk instead of a file confined to the repo — the exact class of "guard present elsewhere but missing here" bug described in the source report, potentially leading to disclosure of file existence/contents outside the repo when the user interacts with the file list.

### Likelihood Explanation
Exploitability depends on whether git/GitHub API data can actually smuggle `../` segments through to `file.path` unnoticed by the user (git itself has protections against writing tree entries with `..` components during checkout, and PR file lists are normally sanitized server-side by GitHub). I was not able to fully verify, within the available tooling, whether the diff-parser (`DiffParser`) or the API response deserialization performs any independent normalization/validation of the `path` field before it reaches these UI components — that would need to be confirmed by reviewing `src/lib/diff-parser.ts` and the PR files-changed API mapping in a live session. Given that a dedicated guard (`resolveWithin`) was added specifically to protect the deep-link `filepath` flow, the same defense-in-depth is arguably warranted, but the reachability of a genuinely malicious path value through git/GitHub data is uncertain from static inspection alone.

### Recommendation
Route all path constructions that combine `repository.path` with file paths sourced from commit/diff/PR data through `resolveWithin` (or `resolveWithinPosix`, since git paths are POSIX-style) before calling `shell.showItemInFolder`/`openFile`, mirroring the check already done in `dispatcher.ts`'s `openRepositoryFromUrl`, and treat a `null` result as "refuse to open" with a log message, consistent with the existing pattern.

### Proof of Concept
Not fully constructible from static analysis alone: exploitation would require confirming that either (a) a git tree/diff entry can carry a `path` containing `../` segments that survives `DiffParser`/status parsing unmodified, or (b) the PR "files changed" API data is used verbatim without validation. Concretely reachable code path (assuming a crafted `file.path` reaches the UI): open a PR/commit containing a file record with `path = "../../secret"`, select it in `PullRequestFilesChanged`/`SelectedCommits`, and trigger "Reveal in Finder/Explorer" or double-click to open — `Path.join(repository.path, "../../secret")` resolves outside the repo, and unlike `openRepositoryFromUrl`, no `resolveWithin` check intercepts it before `shell.showItemInFolder`/`openPath` is invoked. [11](#0-10) [12](#0-11)

### Citations

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

**File:** app/src/lib/app-shell.ts (L16-24)
```typescript
  /**
   * Reveals the specified file using the operating
   * system default application.
   * Do not use this method with non-validated paths.
   *
   * @param path - The path of the file to open
   */

  readonly openPath: (path: string) => Promise<string>
```

**File:** app/src/lib/app-shell.ts (L55-63)
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L86-97)
```typescript
  private onOpenFile = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    this.onOpenBinaryFile(fullPath)
  }

  /**
   * Opens a binary file in an the system-assigned application for
   * said file type.
   */
  private onOpenBinaryFile = (fullPath: string) => {
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L154-191)
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
```

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx (L282-288)
```typescript
  private onRowDoubleClick = (row: number) => {
    const file = this.getCommittedFiles()[row]
    if (file !== undefined) {
      const fullPath = Path.join(this.props.repository.path, file.path)
      openFile(fullPath, this.props.dispatcher)
    }
  }
```

**File:** app/src/lib/get-old-path.ts (L1-16)
```typescript
import { FileChange, AppFileStatusKind } from '../models/status'

/**
 * Resolve the old path (for a rename or a copied change) or default to the
 * current path of a file
 */
export function getOldPathOrDefault(file: FileChange) {
  if (
    file.status.kind === AppFileStatusKind.Renamed ||
    file.status.kind === AppFileStatusKind.Copied
  ) {
    return file.status.oldPath
  } else {
    return file.path
  }
}
```
