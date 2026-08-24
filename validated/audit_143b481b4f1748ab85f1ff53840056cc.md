### Title
Symlink-based path traversal via `revealInFileManager` / `openFile` on attacker-controlled repository contents - ([File: app/src/lib/app-shell.ts])

### Summary
GitHub Desktop has a purpose-built path-containment helper, `resolveWithin()` in `app/src/lib/path.ts`, which resolves a relative path against a root and uses `realpath` to reject the result if it escapes the root **through a symlink**. [1](#0-0) 
This helper is exercised in tests specifically for the symlink-escape case. [2](#0-1) 

It is used to sanitize the deep-link "open file" flow and the Copilot conflict-context file reader. [3](#0-2) [4](#0-3) 

However, the shared helper actually invoked by "Reveal in Finder/Explorer" and "Open with Default Program" context-menu actions, `revealInFileManager()`, does **not** use `resolveWithin`. It simply does `Path.join(repository.path, path)` and hands the result straight to `shell.showItemInFolder`: [5](#0-4) 

This helper is called with `file.path` values taken directly from `git status`/`git diff` output (attacker-influenced file names inside a cloned/fetched repository) in numerous UI components, none of which perform any traversal/symlink check first: [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9) 

The "Open with Default Program" action reuses the same unvalidated `fullPath` and passes it to `shell.openExternal('file://' + fullPath)`: [11](#0-10) [12](#0-11) 

### Finding Description
Git allows a repository to contain a symbolic-link blob whose **target text** is attacker-controlled and can point anywhere on the filesystem (e.g. `../../../../../../etc/passwd` or an absolute path such as `/Users/victim/.ssh/id_rsa`). Git's tree-entry name protections (rejecting `..` in path *components*) do not restrict the *contents* of a symlink blob, so a normal `git clone`/`git fetch`/`git checkout` of a malicious repository will materialize such a symlink inside the working directory without error.

Once checked out, `git status` (and diff output) reports the symlink using its in-repo relative path (e.g. `innocuous-name`). Every UI surface that offers "Reveal in Finder/Explorer", "Open with Default Program", or the Copilot conflict overflow menu computes:

```
fullPath = Path.join(repository.path, file.path)
```

and passes that string directly to `shell.showItemInFolder` / `shell.openExternal` / `openFile`. Because `Path.join` performs no filesystem resolution, `fullPath` is syntactically inside the repo, but the OS (Finder/Explorer, or the file-open call) follows the symlink at open-time and operates on the real target — which can be anywhere on disk.

This is precisely the broken invariant identified by the reporters of CVE-2021-41773: a path that is nominally scoped to an allowed directory is not actually confined there once symlink resolution/normalization happens later in the pipeline. Desktop's own `resolveWithin()` function and its dedicated symlink-escape unit test prove the team is aware of this exact class of bug, but the fix was only applied to two call sites (deep-link file open, Copilot conflict reader) and not to the much more commonly used `revealInFileManager`/`openFile` menu actions.

### Impact Explanation
Path/symlink traversal here does not enable arbitrary code execution by itself, but "Reveal in Finder/Explorer" will open the OS file browser at the symlink's real target location, and "Open with Default Program" will open (or, depending on the file type and default handler, execute) whatever the symlink points to. A malicious repository (delivered via a normal clone/fetch, a shared branch, or a pull request the victim opens in Desktop) can therefore:
- Cause the victim's file explorer to reveal/list sensitive directories or files outside the repo (e.g. `~/.ssh`, `~/.aws`), disclosing file existence, names, and metadata.
- Cause "Open with Default Program" to open a sensitive file (e.g. a private key, `.netrc`, or a script) with its associated application, potentially displaying secret contents on-screen or, for certain file types/handlers, triggering execution.
This matches the accepted impact classes: file read/disclosure outside the repository root, and a step toward code execution depending on the target file type and OS handler behavior.

### Likelihood Explanation
The attack requires no special privileges: any repository the user clones, fetches, or opens a PR/branch from can carry the malicious symlink. Triggering it only needs the victim to perform an action they already do routinely — right-click a changed/committed file and choose "Reveal in Finder/Explorer" or "Open with Default Program" (both are ordinary, expected workflows in the Changes tab, History tab, PR file list, and conflict resolution UI). No unnatural steps, local access, or pre-existing compromise are required, and the existing `resolveWithin` guard demonstrates the vulnerability class is already recognized internally but not applied uniformly.

### Recommendation
Route every `Path.join(repository.path, file.path)` construction that feeds `shell.showItemInFolder`, `shell.openExternal`/`openFile`, or similar OS-level file operations through `resolveWithin()` (or an equivalent realpath-based containment check) before use, rejecting the action (with a user-facing error) when the resolved path escapes the repository root — mirroring what `dispatcher.openRepositoryFromUrl` and `copilot-conflict-context.ts` already do. At minimum this includes `revealInFileManager` in `app/src/lib/app-shell.ts`, and its callers in `selected-commits.tsx`, `filter-changes-list.tsx`, `sidebar.tsx`, `unmerged-file.tsx`, `copilot-conflicts-dialog.tsx`, and `pull-request-files-changed.tsx`.

### Proof of Concept
1. Attacker creates a repository containing a symlink entry, e.g. `git checkout -b main && ln -s /Users/victim/.ssh id_rsa-link && git add id_rsa-link && git commit -m "add"` (or the Windows equivalent target), and hosts/pushes it.
2. Victim clones or fetches the repository in GitHub Desktop, or opens a PR/branch containing this commit; the symlink `id_rsa-link` is checked out into the working directory as-is (git does not block symlink targets that traverse outside the repo).
3. Victim sees `id_rsa-link` listed as a file (e.g. in the Changes tab, History tab, or PR "Files changed" view) and right-clicks it, selecting "Reveal in Finder"/"Show in Explorer" or "Open with Default Program".
4. `revealInFileManager(repository, 'id_rsa-link')` computes `fullPath = Path.join(repo.path, 'id_rsa-link')` and calls `shell.showItemInFolder(fullPath)` — the OS follows the symlink and opens/reveals `~/.ssh`, exposing files outside the intended repository sandbox, with no traversal check ever performed. [13](#0-12)

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

**File:** app/src/ui/history/selected-commits.tsx (L405-410)
```typescript
    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
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

**File:** app/src/ui/lib/conflicts/unmerged-file.tsx (L407-418)
```typescript
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L185-190)
```typescript
    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
```

**File:** app/src/ui/lib/open-file.ts (L4-16)
```typescript
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
