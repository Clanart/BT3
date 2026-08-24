### Title
Symlinked working-tree files let a malicious repository read/execute files outside the repo via "Open with Default Program" / "Reveal in Finder" - (File: app/src/lib/app-shell.ts)

### Summary
`revealInFileManager` and the `openFile` context-menu action build the on-disk path for a tracked file with a plain `Path.join(repository.path, file.path)` and never verify that the resulting path — after the OS resolves symlinks — actually stays inside the repository. Git fully supports committing symlinks (mode `120000`), so a cloned/fetched malicious repository can ship a tracked "file" that is really a symlink pointing outside the repository. When the victim right-clicks that entry and chooses "Open with Default Program" or "Reveal in File Manager", the shell follows the symlink and operates on the real target, not the repo-relative path the app validated.

### Finding Description
`revealInFileManager` computes the path to hand to the OS shell without any containment check: [1](#0-0) 

The same unguarded pattern is used by every "Open with Default Program" / "Reveal in File Manager" context-menu entry for working-directory changes, committed files and conflicted files: [2](#0-1) [3](#0-2) [4](#0-3) 

The "Open with Default Program" handler simply forwards the joined path to `shell.openExternal('file://' + fullPath)`, which delegates to the OS shell open, which follows symlinks and resolves the file to whatever it truly points to: [5](#0-4) 

`isSafeFileExtension` is checked against the *tracked file name* (the symlink's own name, e.g. `notes.pdf`), not against the type of the file the symlink resolves to, so the extension gate does not protect against this: an attacker just names the symlink with a "safe" extension while pointing it at an executable, script, or sensitive file outside the repo. Contrast this with `openRepositoryFromUrl`, which handles an analogous "open a repo-relative file path" operation but explicitly guards against traversal and symlink escape using `resolveWithin` before calling `shell.showItemInFolder`: [6](#0-5) 

`resolveWithin` itself is specifically designed (and tested) to reject paths that escape the root via a symlink: [7](#0-6) 

The broken invariant: every code path that turns a user-facing repository-relative file path into an OS "open/reveal" action is supposed to keep the operation confined to the repository working directory — exactly the invariant `resolveWithin` enforces for `openRepositoryFromUrl`. The `revealInFileManager`/`openFile` context-menu paths never apply that same guard, so a tracked symlink silently breaks the confinement invariant the moment the OS resolves it.

### Impact Explanation
An attacker who controls a repository the victim clones or fetches (a fork, a PR branch checked out via "Open in Desktop", etc.) can commit a symlink such as `notes.pdf -> /Users/victim/.ssh/id_rsa` or `notes.pdf -> /Users/victim/Library/Application Support/<browser>/Login Data`, or on Windows point it at an executable. When the victim, browsing the Changes/History/Conflicts list, right-clicks and selects "Open with Default Program", Desktop opens the *resolved* target, not the intended in-repo file — leaking file contents or, if the symlink is retargeted to an executable/script, causing it to run. This is a file-read (and potentially code-execution) primitive fully attacker-controlled from within the cloned/fetched repository content, matching the "attacker controls a cloned/fetched repository ... code execution, file write or read outside the repo" impact category.

### Likelihood Explanation
Likelihood is moderate-to-high: no special local access or admin rights are needed, only that the victim opens/clones an attacker-supplied repository (a common, expected Desktop workflow — cloning a fork, opening a PR from a URL, etc.) and performs a normal, single context-menu click ("Open with Default Program" or "Reveal in File Manager") on a changed/committed file — an action Desktop actively surfaces in its UI. Git natively supports and preserves symlinks on checkout on macOS/Linux (and, when `core.symlinks` is enabled, on Windows), so no unusual git configuration is required for the payload to materialize on disk.

### Recommendation
Before handing a repository-relative path to `shell.showItemInFolder` or `shell.openExternal`, resolve it the same way `openRepositoryFromUrl` does: use `resolveWithin(repository.path, file.path)` (or an equivalent realpath-based containment check) and refuse the action if the resolved path escapes the repository root. Apply this uniformly in `revealInFileManager` (`app/src/lib/app-shell.ts`) and in `openFile` (`app/src/ui/lib/open-file.ts`), rather than only in the "open-repository-from-url" flow.

### Proof of Concept
1. Attacker creates a repository containing a tracked symlink, e.g. `git symlink notes.pdf -> ../../../../.ssh/id_rsa` (or any absolute/relative path escaping the repo), commits and pushes it (or hosts it as a fork/PR).
2. Victim clones the repository in GitHub Desktop, or opens the attacker's PR via "Open in Desktop" (`openRepositoryFromUrl`) and browses to the file in Changes/History.
3. Victim right-clicks `notes.pdf` and selects "Open with Default Program" (or "Reveal in File Manager").
4. `Path.join(repository.path, 'notes.pdf')` produces a path inside the repo, but the OS shell (`open`/`explorer`/`xdg-open`) follows the symlink and opens/reveals the real target (`~/.ssh/id_rsa` or an attacker-chosen executable) — content or code execution occurs outside the repository boundary, with no traversal check ever performed by Desktop, unlike the equivalent `resolveWithin`-guarded code path used for `openRepositoryFromUrl`.

### Citations

**File:** app/src/lib/app-shell.ts (L61-64)
```typescript
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

**File:** app/src/ui/history/selected-commits.tsx (L405-420)
```typescript
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

**File:** app/src/ui/lib/open-file.ts (L1-16)
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
