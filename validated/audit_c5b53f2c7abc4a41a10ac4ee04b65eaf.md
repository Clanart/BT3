### Title
Path traversal via git-tracked symlink in "Reveal in Finder" / "Open with default program" — missing `resolveWithin` guard - ([File: app/src/lib/app-shell.ts])

### Summary
GitHub Desktop lets an attacker-controlled repository ship a tracked symlink (git mode `120000`) whose target points outside the repository (e.g. `../../../../etc` or an absolute path to a sensitive user directory). When the victim clones/opens that repository and later right-clicks the symlink entry in the Changes list, History view, or a PR "Files changed" view and selects **Reveal in Finder** or **Open with default program**, Desktop resolves the path with a naive `Path.join(repository.path, path)` and hands it straight to Electron's `shell.showItemInFolder`/`shell.openPath`, which follow the symlink at the OS level. This is the exact "path traversal using symlink" bug class from the H1 report: an app-controlled root directory join that does not defend against a symlink escaping that root.

### Finding Description
`revealInFileManager` builds the path to reveal purely by string concatenation, with no root-containment check: [1](#0-0) 

This helper is invoked all over the UI with `file.path` values that originate directly from git status, commit diffs, and even from the GitHub API (PR "files changed"), i.e. fully attacker-influenced strings for a repository the attacker authored: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

Critically, Desktop *already recognizes* that joining an untrusted repo-relative path onto `repository.path` needs symlink-aware containment checking, and has a purpose-built helper for it, `resolveWithin`, which resolves the real path (`fs.realpath`) of both the root and the target and rejects anything that escapes the root through a symlink: [6](#0-5) 

That guard is used for the deep-link "open file" flow and for the Copilot conflict-resolution file reader — both of which call `shell.showItemInFolder`/read a file only after `resolveWithin` succeeds: [7](#0-6) [8](#0-7) 

`revealInFileManager` and its many call sites, however, skip this check entirely and just do `Path.join`, even though the "Open with default program" path (`onOpenItem`) only screens the file *extension* of the attacker-chosen path, not the resolved symlink target: [9](#0-8) 

The unit test suite explicitly demonstrates that `Path.join`/`Path.resolve` alone do not stop a symlink escape, and that only `resolveWithin` (via `realpath`) catches it: [10](#0-9) 

### Impact Explanation
An attacker who controls a cloned/fetched repository (or a PR the victim reviews with "Files changed") can commit a symlink whose target resolves outside the repository root. When the victim uses ordinary Desktop UI actions (context-menu "Reveal in Finder", "Open with default program", "Copy File Path") on that entry, Desktop's directory/file resolution follows the symlink outside of `repository.path`. This can expose or open files/directories the user did not intend to browse from within "their repository," and can be chained with `shell.openPath`'s default-application behavior to open/execute whatever the attacker-chosen path on disk happens to be, since the extension gate (`isSafeFileExtension`) only inspects the attacker-supplied symlink name, not the real target. This matches the report's "medium" severity: no code delivered by the attacker is required, but the app's own root-containment invariant is broken by a git object the attacker fully controls.

### Likelihood Explanation
Git natively supports and checks out symlinks (mode `120000`) without extra privileges, so crafting the malicious tree entry requires no special access — a plain `git commit`. Desktop then surfaces the symlinked path as an ordinary change/file entry, and the vulnerable action is one context-menu click away, satisfying the "link the user clicks" / "attacker controls a cloned repository" valid-impact criteria and requiring no local/admin access.

### Recommendation
Route `revealInFileManager` (and the "open with default program" / "copy path" actions in `filter-changes-list.tsx`, `selected-commits.tsx`, `pull-request-files-changed.tsx`, `unmerged-file.tsx`) through the same `resolveWithin` containment check already used in `dispatcher.ts` and `copilot-conflict-context.ts` before calling `shell.showItemInFolder`/`shell.openPath`, rejecting (and logging) any path whose real, symlink-resolved location falls outside `repository.path`.

### Proof of Concept
1. Attacker creates a repo containing a tracked symlink, e.g. `git symlink-add secrets -> /Users/victim/Library/Application Support/GitHub Desktop` (or `../../../../..` on any OS), and pushes it.
2. Victim clones the repo in GitHub Desktop and opens the Changes/History view where `secrets` appears as a normal entry (`file.path = "secrets"`).
3. Victim right-clicks it and chooses "Reveal in Finder" (`revealInFileManager(repository, "secrets")` in [11](#0-10) ).
4. `Path.join(repository.path, "secrets")` produces `<repo>/secrets`; `shell.showItemInFolder` resolves the symlink at the OS level and opens the out-of-repo target directory in Finder/Explorer — no `resolveWithin`/`realpath` check ever runs, unlike the equivalent deep-link code path in `dispatcher.ts`.

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

**File:** app/src/ui/history/selected-commits.tsx (L398-420)
```typescript
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L185-190)
```typescript
    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
```

**File:** app/src/ui/lib/conflicts/unmerged-file.tsx (L408-418)
```typescript
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
