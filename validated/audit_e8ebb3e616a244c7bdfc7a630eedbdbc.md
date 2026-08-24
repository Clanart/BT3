## Analysis

This confirms a real pattern in the codebase: multiple UI call sites build filesystem paths from git-reported file paths (`file.path`, which for a fetched/checked-out repository or an opened pull request's file list is attacker-controlled content) using a raw `Path.join(repository.path, file.path)`, with **no** call to the traversal-guard `resolveWithin()` that the codebase itself introduces and enforces elsewhere (e.g. `app/src/ui/dispatcher/dispatcher.ts:1963` and `app/src/lib/copilot-conflict-context.ts:393`). [1](#0-0) [2](#0-1) [3](#0-2) 

The `app-shell.ts` docstring explicitly warns callers: *"Do not use this method with non-validated paths"* for both `openPath` and `showItemInFolder`, and yet `revealInFileManager` itself performs no validation, and every call site (`selected-commits.tsx`, `pull-request-files-changed.tsx`, `filter-changes-list.tsx`, `copilot-conflicts-dialog.tsx`) passes the raw git-reported `file.path` straight through `Path.join`. This is the same class of bug as the seed report: a guard that looks like it should stop unsafe input exists conceptually elsewhere in the codebase (`resolveWithin`), but the actual code path that handles this specific operation never invokes it — an incomplete/broken invariant, not a logic-tautology per se, but functionally identical in effect: unsafe input reaches a sink unguarded because the "sign/validity determination" step is missing here while present in sibling code paths.

I was not able to fully verify, within the remaining budget, whether `status-parser.ts` or the diff/rename-detection code enforces any additional sanitization on `file.path` before it reaches the UI (e.g., rejecting `..` components or backslash-as-separator tricks on Windows). This is the key open question for confirming exploitability, and it should be verified by a Devin agent with full read access before treating this as conclusively exploitable.

### Title
Unvalidated repository-relative file paths reachable via `Path.join` bypass the `resolveWithin` traversal guard in file-reveal/open UI actions - (File: app/src/lib/app-shell.ts)

### Summary
`revealInFileManager()` in `app/src/lib/app-shell.ts` and multiple UI call sites (`selected-commits.tsx`, `pull-request-files-changed.tsx`, `filter-changes-list.tsx`, `copilot-conflicts-dialog.tsx`, `copilot-conflicts-changes.tsx`) construct absolute filesystem paths from git-status/diff/PR-reported relative paths using `Path.join(repository.path, file.path)` and pass the result to `shell.showItemInFolder` or `shell.openPath` (via `openFile`) without ever calling the codebase's own traversal-safety primitive, `resolveWithin()`, which is used elsewhere (`dispatcher.ts`, `copilot-conflict-context.ts`) for exactly this purpose.

### Finding Description
The Desktop codebase contains a dedicated guard, `resolveWithin()` (`app/src/lib/path.ts:36-99`), specifically built to prevent path-traversal and symlink-escape when joining a repository-relative path with the repository root — it resolves the path, checks for null bytes, and verifies the `realpath()` of the result stays within the `realpath()` of the root. This guard is correctly used in `dispatcher.ts` when opening a file from a deep-link action, and in `copilot-conflict-context.ts` when reading conflicted files from disk. [4](#0-3) [5](#0-4) 

However, `revealInFileManager()` — the function backing "Reveal in Finder/Explorer" across the Changes list, History, and Pull Request file views — performs no such check:

```ts
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
}
``` [6](#0-5) 

The same unguarded pattern is repeated for "Open with Default Program" (`openFile`) at several sites, all sourcing `path` from `file.path` on `WorkingDirectoryFileChange` / `CommittedFileChange` objects, which are populated from `git status`/`git diff`/PR file-list output — i.e., from content the repository owner (attacker, in the threat model of a malicious/forked repo or PR) fully controls: [2](#0-1) [7](#0-6) [8](#0-7) 

The corrupted value here is `fullyQualifiedFilePath` / `fullPath` — it is trusted to be inside `repository.path` on the strength of `Path.join`'s normalization alone, but `Path.join` does not reject `..` traversal segments and (unlike `resolveWithin`) never calls `realpath()`, so it cannot detect a symlinked directory used to escape the repository root. The existing guard (`resolveWithin`) that would catch this is simply never invoked on this call path — the same "guard exists in principle, doesn't apply in practice" failure mode as the seed report's always-false sign condition.

### Impact Explanation
If a crafted/attacker-controlled repository or pull request can produce a tracked file whose reported path resolves outside the repository root (via `..` segments, or via a symlinked working-directory component combined with Node's non-realpath-checked `Path.join`), an unprivileged user who simply right-clicks a file in the Changes/History/PR-files list and chooses "Reveal in Finder/Explorer" or "Open with Default Program" causes Desktop to reveal or open an arbitrary path on disk chosen by the attacker. This falls squarely within the accepted impact categories: "file write or read outside the repo" and, via `openPath`'s "open with OS default handler" semantics, a step toward code execution if the resolved path points at an executable or script.

### Likelihood Explanation
The likelihood depends entirely on whether git itself, or `status-parser.ts`/diff parsing, already rejects `..`-containing or otherwise traversal-capable paths before they reach the UI layer. Git's index format has historical protections against `..` path components, but there is prior art (e.g., Windows backslash-as-separator tricks, symlinked working-tree directories) for this class of bug in git clients. I could not fully confirm within this session whether `status-parser.ts` sanitizes such inputs — this must be verified against the actual parser logic and against git's own protections before assigning a final severity/likelihood, since if git-core protections make a traversal-path physically impossible to check out, this finding is only a defense-in-depth gap rather than an exploitable bug.

### Recommendation
Route every `Path.join(repository.path, file.path)` construction that is subsequently passed to `shell.showItemInFolder`, `shell.openPath` (via `openFile`), or the external-editor launcher through `resolveWithin(repository.path, file.path)`, refusing the action (with a logged error, mirroring the pattern already used in `dispatcher.ts`'s `openRepositoryFromUrl`) if the resolved path is `null`. This should cover at minimum: `app/src/lib/app-shell.ts:revealInFileManager`, `app/src/ui/history/selected-commits.tsx:onOpenItem`/`onContextMenu`, `app/src/ui/open-pull-request/pull-request-files-changed.tsx:onFileContextMenu`, `app/src/ui/changes/filter-changes-list.tsx`, and `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-*.tsx`.

### Proof of Concept
Conceptual PoC (requires confirming step 1 against the actual status/diff parser, which I could not do in this session):
1. Craft a repository/PR containing a tracked entry whose path, as reported by `git status`/`git diff`/the PR files API, contains directory-traversal segments or resolves (once joined with the repo root) outside the repository — e.g. via a symlinked directory in the working tree that a rename/checkout places in the file list.
2. Victim clones/fetches/opens the PR for this repository in Desktop.
3. Victim right-clicks the file in the Changes, History, or "Files changed" pane and selects "Reveal in Finder/Explorer" or "Open with Default Program".
4. `Path.join(repository.path, file.path)` in `app-shell.ts` (or the equivalent inline `Path.join` in the UI components) produces a path outside `repository.path`; `shell.showItemInFolder`/`shell.openPath` operates on that out-of-repo path with no `resolveWithin` check to stop it.

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

**File:** app/src/ui/history/selected-commits.tsx (L292-295)
```typescript
  private onOpenItem = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    openFile(fullPath, this.props.dispatcher)
  }
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

**File:** app/src/ui/changes/filter-changes-list.tsx (L628-655)
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
  }
```
