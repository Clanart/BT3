Based on the investigation, the strongest and most direct analog to the report's "broken invariant" pattern — a security-relevant value computed from partial state while silently discarding a component needed for correctness — is the inconsistent use of the `resolveWithin` path-containment guard across GitHub Desktop's codebase.

### Title
Untrusted file paths from PR "files changed" list are opened via raw `Path.join` instead of the `resolveWithin` containment check used elsewhere - ([File: app/src/ui/open-pull-request/pull-request-files-changed.tsx])

### Summary
`app/src/lib/path.ts` defines `resolveWithin()`, a guard that resolves a path against a root and rejects it (returns `null`) if it escapes the root or traverses through a symlink [1](#0-0) . This guard is deliberately used when opening a path that originates from an external/untrusted source — e.g. the `x-github-client://` deep-link `openRepositoryFromUrl` handler explicitly rejects absolute paths and calls `resolveWithin` before calling `shell.showItemInFolder` [2](#0-1) , and the Copilot conflict-resolution file reader uses the same guard with an explicit comment "Guard against path traversal and symlink escapes" [3](#0-2) .

However, `PullRequestFilesChanged` — the component that renders the "Files changed" list for a pull request fetched from the GitHub API — builds the on-disk path for a selected file with plain `Path.join(this.props.repository.path, path)` and no containment check at all, then hands that raw path to `shell.openExternal` (via `openFile`) or to the external-editor launcher: [4](#0-3) [5](#0-4) [6](#0-5) . The same unguarded `Path.join` pattern is repeated in `app/src/ui/lib/open-file.ts` (which wraps `shell.openExternal('file://...')`) [7](#0-6) .

### Finding Description
Just as `DLoopCoreBase.totalAssets()` computed vault value from `getTotalCollateralAndDebtOfUserInBase()` but discarded the debt half of the tuple, Desktop's PR-files-changed view computes a filesystem path using only "repository root + relative path" while discarding the containment/symlink check (`resolveWithin`) that the codebase's own security model requires for paths reaching the app from outside the local git object database (deep links, LLM-conflict content). The `CommittedFileChange.path` values rendered here come from `changesetData.files`, which is populated from a diff between the PR's head/base SHAs — data whose ultimate provenance is the GitHub API/remote for the PR, i.e. an untrusted external object per the task's threat model ("a GitHub API object ... or a git remote/proxy response"). The developer who wrote `dispatcher.ts`'s `openRepositoryFromUrl` recognized that filepaths originating outside the local trusted git plumbing need `resolveWithin`+`isAbsolute` checks; that same discipline was not applied to `onOpenFile`/`onRowDoubleClick`/`onFileContextMenu` in `pull-request-files-changed.tsx`.

### Impact Explanation
If any path segment for a PR's changed file can be manipulated to traverse outside the repository root (through path components not fully normalized/sandboxed by the diff pipeline, or through a symlinked repository-root component that a malicious clone left in place), `Path.join` alone does not prevent the resulting path from resolving outside the working directory the way `resolveWithin` guarantees — `Path.join('/repo', '../../etc/foo')` collapses to `/etc/foo`, whereas `resolveWithin` would reject it via `realpath` comparison against the root [8](#0-7) . The consequence is that "Open with default program" / "Open in external editor" / double-click actions on a PR's file list could read or execute a file outside the intended repository directory, matching the report's category of "code execution/file read outside the repo" driven by attacker-controlled external data.

### Likelihood Explanation
This is lower-confidence than a directly reachable primitive: standard git tree-object validation already rejects `..`/absolute segments in committed blob paths, so the most common exploitation vector (a malicious commit path) is constrained by git itself, and I could not find a call path in this index that lets a raw, unsanitized string bypass that git-level normalization before reaching `CommittedFileChange.path`. What is verifiable and inconsistent, however, is that the developers themselves treat externally-sourced file paths (deep links, LLM conflict content) as needing `resolveWithin`, but did not apply the same guard to the PR-files-changed UI, which is the closest structural analog to the reported bug class (a security decision computed from an incomplete/partial view of the relevant state).

### Recommendation
Route `onOpenFile`, `onOpenBinaryFile`'s callers, `onRowDoubleClick`, and `onFileContextMenu` in `pull-request-files-changed.tsx` through `resolveWithin(repository.path, file.path)` (rejecting `null`/absolute results) before calling `openFile`, `revealInFileManager`, or `dispatcher.openInExternalEditor`, mirroring the guard already applied in `dispatcher.ts`'s `openRepositoryFromUrl` and `copilot-conflict-context.ts`, so that every path derived from repository-external/PR data is validated consistently rather than selectively.

### Proof of Concept
Not independently reproducible from the indexed code alone: I could not confirm a concrete git/GitHub-API mechanism that lets `CommittedFileChange.path` carry a traversal payload past git's own tree-path validation. Given the index's stated coverage limits, a Devin session with full repository access would be needed to trace `getChangedFiles`/PR diff construction end-to-end and confirm whether any code path (e.g., unusual PR diff rename/copy metadata) can inject a `..`-bearing or symlinked path into `files` before it reaches `Path.join` in `pull-request-files-changed.tsx`.

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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L162-194)
```typescript
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L234-239)
```typescript
  private onRowDoubleClick = (row: number) => {
    const files = this.props.files
    const file = files[row]

    this.props.onOpenInExternalEditor(file.path)
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
