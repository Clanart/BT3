## Title
Unbounded path traversal when opening pull-request diff files from GitHub API-derived file paths - ([File: app/src/ui/open-pull-request/pull-request-files-changed.tsx])

## Summary
The report's core issue is that an external, attacker-influenced input (an array element) is trusted and used directly without bounds/validation before being passed to a sensitive operation. The GitHub Desktop analog is a trust boundary violation of the same shape: a `CommittedFileChange.path` value that ultimately originates from pull-request diff/changed-file data (i.e., data associated with a PR — potentially from a fork the attacker controls) is joined directly onto the repository's filesystem root without going through the app's own `resolveWithin` traversal guard, unlike other parts of the codebase that handle the same class of untrusted path input.

## Finding Description
`PullRequestFilesChanged` builds an absolute filesystem path directly from `file.path` using plain `Path.join`, with no traversal or symlink-escape check: [1](#0-0) [2](#0-1) 

The resulting `fullPath` is then handed to `revealInFileManager`, `dispatcher.openInExternalEditor(fullPath)`, `clipboard.writeText(fullPath)`, and `openFile(fullPath, dispatcher)` (which calls `shell.openExternal('file://' + fullPath)`): [3](#0-2) [4](#0-3) 

This is the same "unvalidated externally-influenced element used directly in a sensitive sink" pattern as the reported bug, just applied to a file path instead of a loop counter. Critically, the codebase already recognizes this exact class of risk and has fixed it elsewhere: `openRepositoryFromUrl` in the dispatcher explicitly rejects absolute paths and calls `resolveWithin(repository.path, filepath)` before touching the filesystem, and `copilot-conflict-context.ts` does the same for conflict file paths: [5](#0-4) [6](#0-5) 

`resolveWithin` performs `realpath`-based root containment checks and rejects null bytes, absolute-path escapes, and symlink traversal: [7](#0-6) 

`PullRequestFilesChanged`, however, was not updated to use this guard, so a `CommittedFileChange.path` value that contains `../` segments (or, on Windows, a path that escapes via a symlinked directory) would resolve outside the repository root when joined via `Path.join(repository.path, file.path)`.

## Impact Explanation
If a malicious PR's changed-file listing can produce a `file.path` value containing directory-traversal segments, a user who views that PR's "Files changed" tab in Desktop and interacts with a file (double-click to open in editor, "Open with default program", "Reveal in File Manager", or even "Copy file path") would have Desktop construct and act on a path outside the repository — potentially opening/executing an arbitrary file on disk chosen by the PR author, or leaking an arbitrary file's location via clipboard/file manager. This matches the required impact class: attacker (PR author/fork owner) controls a GitHub API object (the PR's changed files), and the result is file read/execution outside the repo.

## Likelihood Explanation
I could not fully verify, in the time available, whether the `file.path` values reaching `PullRequestFilesChanged` are always constrained by a prior local `git diff` (which would inherit git's own tree-path validation) or can, in the `nonLocalCommitSHA` case, come directly from unvalidated GitHub API diff data before the corresponding commit is fetched locally. This determines whether the traversal string could actually reach this component un-sanitized, or whether it's only reachable after git itself has already validated the path as part of a real tree object. Because of this open question, likelihood is best characterized as uncertain/medium rather than confirmed-high, and should be verified against how `commitSelection.changesetData` is populated for PRs whose head commit isn't yet fetched locally.

## Recommendation
- Route `file.path` through `resolveWithin(repository.path, file.path)` (as already done in `dispatcher.ts` and `copilot-conflict-context.ts`) before calling `Path.join`, `shell.openExternal`, `revealInFileManager`, `openInExternalEditor`, or writing it to the clipboard, in `pull-request-files-changed.tsx`.
- Reject absolute paths (mirroring the `isAbsolute(filepath)` check used in `openRepositoryFromUrl`).
- Audit whether `changesetData.files` for a PR with a `nonLocalCommitSHA` is ever populated from raw GitHub API diff data rather than local git output, and if so, apply the same guard at that data-loading boundary, not just at the UI layer.

## Proof of Concept
Conceptual (could not be executed without filesystem/tool access):
1. Attacker opens a PR (or a PR from a fork) whose diff includes a changed file whose path, as surfaced through the changed-files data reaching `PullRequestFilesChanged`, contains traversal segments (e.g., `../../../../.ssh/id_rsa` or platform-specific equivalents).
2. Victim opens "Open Pull Request" / "Files changed" view in Desktop for that PR.
3. Victim double-clicks the file or uses "Open with default program" / "Reveal in File Manager".
4. `Path.join(repository.path, file.path)` resolves to a path outside `repository.path`, and `openFile`/`revealInFileManager`/`openInExternalEditor` acts on that out-of-repo path with no `resolveWithin` check, unlike the equivalent flows in `dispatcher.ts` and `copilot-conflict-context.ts`.

### Citations

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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L154-164)
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
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L185-211)
```typescript
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
      { type: 'separator' },
      {
        label: CopyFilePathLabel,
        action: () => clipboard.writeText(fullPath),
      },
      {
        label: CopyRelativeFilePathLabel,
        action: () => clipboard.writeText(Path.normalize(file.path)),
      },
      { type: 'separator' },
    ]
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
