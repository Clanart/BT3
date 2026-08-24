## Title
Unvalidated commit/working-tree file paths bypass the app's own path-containment guard when revealing files or launching the external editor - (File: `app/src/lib/app-shell.ts`)

## Summary
GitHub Desktop already recognizes that file paths sourced from repository content (commits, conflicts, deep links) must be validated before being turned into filesystem operations, and it implements a dedicated containment check, `resolveWithin`, for that purpose [1](#0-0) . However, that guard is only applied on some call sites (URL-triggered file opens and Copilot conflict-context reads) and is missing on the call sites that reveal or open files derived directly from git history / working-directory data, which is exactly the class of value an attacker controls via a crafted commit in a cloned/fetched repository. This mirrors the report's core defect: an invariant ("stay inside the trusted boundary") is checked in one code path but not re-verified after the value is produced/derived through another path that also needs it.

## Finding Description
`revealInFileManager` explicitly documents the required invariant in its own doc comment for the underlying shell API ("Do not use this method with non-validated paths") [2](#0-1) , yet its implementation builds the final path with a bare `Path.join` and no containment check at all: [3](#0-2) 

The `path` argument passed in comes from `CommittedFileChange.path`, which is populated verbatim from `git log --raw -z` output with no sanitization for path-traversal segments: [4](#0-3) 

That unsanitized `path` is then joined with the repository root and used to call `shell.showItemInFolder`, `clipboard.writeText`, and to launch the user's external editor in `app/src/ui/history/selected-commits.tsx`: [5](#0-4) 

The same unguarded pattern (`Path.join(repository.path, path)` with no `resolveWithin`) is repeated for external-editor launches from the main renderer and from the Copilot conflict-resolution overflow menu: [6](#0-5) [7](#0-6) 

By contrast, the code base demonstrates the developers know this needs to be checked: the deep-link handler validates that a URL-supplied path resolves inside the repo before revealing it, rejecting absolute paths and paths that escape the root via `resolveWithin`: [8](#0-7) 

and the newer Copilot-conflict-context builder applies the identical guard before reading file contents: [9](#0-8) 

So the "fix" (the `resolveWithin` containment check) exists in the codebase but was never propagated to the sibling code paths that consume the same class of attacker-influenced value (a path recorded inside a git commit object), exactly analogous to the Hyperdrive report where the negative-interest inequality was enforced before curve trading but not re-verified after fees were subsequently applied to the same reserves.

## Impact Explanation
`file.path`/`oldPath` values are taken from tree entries of arbitrary commits in a cloned or fetched repository, which is fully attacker-controlled content (a malicious repo owner, or any repository the victim adds/clones/fetches, including PR branches). If such an entry encodes a `..`-style traversal (or is otherwise able to reach outside the working directory once joined), the unguarded `Path.join` in `revealInFileManager`/`onOpenInExternalEditor` will operate on a location outside the repository: `shell.showItemInFolder`/`shell.openPath` will act on that external file, and `launchExternalEditor`/`launchCustomExternalEditor` will spawn the user's configured editor with that path as an argument, e.g. `spawn('open', ['-a', editorPath, fullPath])` [10](#0-9) . Depending on the editor/file type this can range from disclosure of files outside the repo to launching arbitrary local content in a way the editor may execute or auto-run (e.g., editor plugins/workspace config auto-load).

## Likelihood Explanation
The path never passes through any repo-containment check on this route, so the only backstop is whatever protections `git` itself applies to path components inside tree/diff objects it writes out via `log --raw`; those protections are oriented around checkout-time filesystem safety (`core.protectNTFS`/`protectHFS`, rejecting literal `..`), not necessarily every code path that merely prints recorded names. Given the app explicitly built and uses `resolveWithin` elsewhere for the identical purpose, the omission here indicates the guard was added reactively for the URL/Copilot code paths without auditing the older, more central history/commit-viewing UI that reaches the exact same `app-shell.ts` primitives.

## Recommendation
Route every path derived from repository content (`CommittedFileChange.path`/`oldPath`, working-directory/conflict file paths, stash file paths) through `resolveWithin(repository.path, path)` before calling `revealInFileManager`, `onOpenInExternalEditor`, `openFileInExternalEditor`, or writing the path to the clipboard, mirroring the guard already implemented in `dispatcher.ts`'s `openRepositoryFromUrl` and `copilot-conflict-context.ts`. Reject (or surface an error for) any path whose resolved value falls outside the repository root instead of silently joining and using it.

## Proof of Concept
1. Create a git repository and, using low-level plumbing (`git hash-object`/`git mktree`/`update-index --index-info`) or a `fast-import` stream, craft a commit whose tree/raw-diff entry effectively causes the `path` string returned by `git log --raw -z` to contain traversal segments (e.g. an entry recorded via a rename/copy pair or an unusual encoding that survives `parseRawLogWithNumstat`'s straightforward string extraction at `app/src/lib/git/log.ts:307`).
2. Have the victim clone/fetch this repository into GitHub Desktop and browse History, selecting the crafted commit.
3. Right-click the file entry and choose "Show in Finder/Explorer" or "Open in <editor>".
4. Observe that `selected-commits.tsx` builds `fullPath = Path.join(repository.path, file.path)` with no `resolveWithin` check and passes it straight to `revealInFileManager`/`onOpenInExternalEditor`, which reveal/launch the resolved location — confirming that, unlike the deep-link and Copilot-conflict code paths, this route never re-validates the joined path stays inside the repository root.

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

**File:** app/src/lib/git/log.ts (L299-316)
```typescript
      const status = forceUnwrap(
        'Invalid log output (status)',
        lineComponents.at(-1)
      )
      const oldPath = /^R|C/.test(status)
        ? forceUnwrap('Missing old path', lines.at(++i))
        : undefined

      const path = forceUnwrap('Missing path', lines.at(++i))

      files.push(
        new CommittedFileChange(
          path,
          mapStatus(status, oldPath, srcMode, dstMode),
          sha,
          parentCommitish
        )
      )
```

**File:** app/src/ui/history/selected-commits.tsx (L384-415)
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

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L212-223)
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

**File:** app/src/lib/editors/launch.ts (L34-36)
```typescript
    const child = spawnAsDarwinApp
      ? spawn('open', ['-a', editorPath, ...args], opts)
      : spawn(editorPath, args, opts)
```
