Found a concrete analog. The external report's core invariant is "perform a sensitive operation on an attacker-influenced target without validating that it stays within the intended trust boundary." The GitHub Desktop equivalent is file-system operations that join a repository-relative path from committed file data with `repository.path` **without** validating the resolved path stays inside the repository — even though the codebase already has a purpose-built, tested safe helper (`resolveWithin`) for exactly this class of bug, and even an explicit warning comment saying not to skip it.

### Title
Unvalidated file-path join lets a malicious repository's checked-out symlink redirect "Open"/"Reveal"/"Copy Path" actions outside the repository - (File: app/src/lib/app-shell.ts)

### Summary
Several UI actions that operate on files from a commit's change list (History view, Open Pull Request "Files changed" view) build the file's on-disk path with a plain `Path.join(repository.path, file.path)` and then pass that path to `revealInFileManager`, `openFile`/`onOpenItem`, `dispatcher.openInExternalEditor`, or `clipboard.writeText`. Unlike other, newer call sites in the same codebase (`app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/copilot-conflict-context.ts`), these paths are never passed through `resolveWithin`, the helper explicitly built (and unit-tested) to reject paths that escape the repository root via `..` segments or via a symlink component.

### Finding Description
`revealInFileManager` documents the exact hazard and then ignores its own warning: [1](#0-0) 

The two main call sites that reach it use `pathExists()` only as a UI-enablement check, not a security boundary — `pathExists` follows symlinks and returns `true` even when the resolved target is outside the repository: [2](#0-1) [3](#0-2) [4](#0-3) 

The codebase already recognizes and fixes this exact bug class elsewhere with `resolveWithin`, which explicitly resolves symlinks and null-byte/traversal segments and returns `null` if the result escapes the root: [5](#0-4) 

Its use in the deep-link handler shows the intended safe pattern — reject absolute paths, then require `resolveWithin` to succeed before calling `shell.showItemInFolder`: [6](#0-5) 

And `copilot-conflict-context.ts` states the rationale directly: "Guard against path traversal and symlink escapes (cross-platform)": [7](#0-6) 

A dedicated test proves the symlink-escape scenario is a real, previously-considered attack: a symlink inside the working tree that points outside the root causes `resolveWithin` to return `null`, precisely because plain `Path.join` + on-disk symlink resolution would otherwise walk outside the repository: [8](#0-7) 

The unchecked call sites (`selected-commits.tsx`, `pull-request-files-changed.tsx`, `app-shell.ts`) never apply this validation, so the "corrupted value" is `fullPath`/`fullyQualifiedFilePath` — a value that is trusted to stay inside `repository.path` but is not actually verified to.

### Impact Explanation
A malicious/compromised repository (or a malicious PR the victim clones/fetches/browses) can commit a directory that is later replaced by a symlink pointing to a sensitive location on the victim's machine (e.g. `~/.ssh`, `~/.aws`), while an earlier commit in history recorded a plausible filename (e.g. `id_rsa`, `credentials`) under that same relative path. When the victim, working in a normally checked-out copy of that repository, browses commit history or an open pull request and right-clicks that file to choose "Reveal in Finder/Explorer", "Open with Default Program", or "Copy File Path" (or double-clicks to open in the external editor), Desktop resolves `repository.path/<attacker chosen relative path>` through the now-present malicious symlink and reveals/opens/copies the path of a real file *outside* the repository — disclosing its location and, via "Open"/"Reveal", its contents to the user's default file manager or editor. This is an unprivileged, repository-content-driven escape of the trust boundary that Desktop is supposed to enforce (as evidenced by `resolveWithin` existing specifically to stop it).

### Likelihood Explanation
Requires only that the victim add/clone/fetch an attacker-controlled repository or open a malicious PR's "Files changed" view and interact with a context menu entry or double-click a file — no admin rights, no pre-existing malware, and no unnatural steps beyond ordinary Desktop usage (browsing history/PR diffs is a core workflow). The `pathExists` gate that currently guards menu-item enablement does not stop this because it follows symlinks rather than confirming containment.

### Recommendation
Route every path derived from repository-relative file data (`CommittedFileChange.path`, `WorkingDirectoryFileChange.path`) through `resolveWithin(repository.path, file.path)` before calling `revealInFileManager`, `openFile`/`onOpenItem`, `dispatcher.openInExternalEditor`, or exposing the path via clipboard — mirroring the pattern already used in `app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/copilot-conflict-context.ts`. Treat a `null` result as "file cannot be safely opened" instead of falling back to the raw `Path.join` result.

### Proof of Concept
1. Attacker creates a public repo with history:
   - Commit 1: adds directory `payload/id_rsa` (arbitrary content) — this appears as a `CommittedFileChange` with `path = "payload/id_rsa"`.
   - Commit 2: deletes `payload/` and replaces it with a symlink `payload -> <relative path back up to victim's home>/.ssh` (a valid git blob of type symlink).
2. Victim clones/fetches the repo with GitHub Desktop and checks out the branch at Commit 2 (so `payload` on disk is now the malicious symlink).
3. Victim opens History, selects Commit 1, and right-clicks the file `payload/id_rsa` shown in the commit's file list (`app/src/ui/history/selected-commits.tsx`).
4. `onContextMenu` computes `fullPath = Path.join(repository.path, "payload/id_rsa")`; `pathExists` follows the symlink and finds the victim's real `~/.ssh/id_rsa`, so the menu items are enabled.
5. Victim clicks "Reveal in Finder/Explorer" or "Open with Default Program"; `revealInFileManager`/`openFile` opens the victim's actual SSH private key file, and "Copy File Path" places its real absolute path on the clipboard — all without any indication the resolved target left the repository.

### Citations

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

**File:** app/src/ui/history/selected-commits.tsx (L384-420)
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
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenItem(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
      },
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L162-200)
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
      },
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenFile(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
      },
```

**File:** app/src/lib/path.ts (L74-100)
```typescript
/**
 * Resolve one or more path sequences into an absolute path underneath
 * or at the given root path.
 *
 * The path segments are expected to be relative paths although
 * providing an absolute path is also supported. In the case of an
 * absolute path segment this method will essentially only verify
 * that the absolute path is equal to or deeper in the directory
 * tree than the root path.
 *
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
 *
 * This method will resolve paths using the current platform path
 * structure.
 *
 * @param rootPath     The path to the root path. The resolved path
 *                     is guaranteed to reside at, or underneath this
 *                     path.
 * @param pathSegments One or more paths to join with the root path
 */
export function resolveWithin(
  rootPath: string,
  ...pathSegments: string[]
): Promise<string | null> {
  return _resolveWithin(rootPath, pathSegments)
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

**File:** app/src/lib/copilot-conflict-context.ts (L390-400)
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
