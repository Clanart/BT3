## Title
Malicious tracked symlink in a cloned repository escapes the repo root via "Reveal in Finder/Explorer" and "Open with default program" context-menu actions - ([File: app/src/ui/changes/sidebar.tsx], [File: app/src/ui/history/selected-commits.tsx], [File: app/src/ui/open-pull-request/pull-request-files-changed.tsx], [File: app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx])

## Summary
Like the Convex `addPool` bug, where a value returned by an untrusted source (`get_gauges`) was accepted without checking for the "zero" sentinel case, GitHub Desktop's file-action context menus accept a file path reported by `git status`/`git show` from an **attacker-controlled repository** and join it directly onto the repository root with `Path.join(repository.path, path)`, without ever resolving/validating that the final on-disk target actually stays inside the repository. Git allows tracking symlinks (mode `120000`); a cloned/fetched malicious repo can contain a tracked symlink such as `notes.txt -> ../../../../.ssh/id_rsa` (or any absolute path). When the path is joined naively, `Path.join` doesn't dereference symlinks, so the check passes, but the actual filesystem object the shell subsequently opens/reveals is outside the repository.

## Finding Description
The codebase already has the correct primitive for this exact problem: `resolveWithin()` in [1](#0-0)  resolves a path relative to a root and, critically, calls `realpath()` on both the root and the resolved path, returning `null` if the symlink-resolved real path escapes the root. This guard is correctly used in two places that handle untrusted repo content:
- `copilot-conflict-context.ts` explicitly comments "Guard against path traversal and symlink escapes" before reading conflict file content [2](#0-1) .
- The deep-link `open-repository-from-url` handler validates `filepath` with `resolveWithin` before calling `shell.showItemInFolder` [3](#0-2) .

However, the far more commonly used file-list context-menu actions do **not** apply this guard. They build the full path with plain `Path.join` and hand it straight to `shell` APIs:
- Changes sidebar "Open with default program": `Path.join(this.props.repository.path, path)` → `openFile(fullPath, dispatcher)` [4](#0-3) .
- History "Reveal in File Manager" / "Open in external editor" / "Open with default program" for committed files: `Path.join(repository.path, file.path)` used for `revealInFileManager`, `onOpenInExternalEditor`, and `onOpenItem` [5](#0-4) .
- Pull-request files-changed view (content is the fork/PR author's tree, fully attacker-controlled before merge): identical pattern [6](#0-5) , [7](#0-6) .
- Copilot conflict resolution overflow menu: `join(repository.path, path)` → `revealInFileManager`/`openFile`/external editor [8](#0-7) .

`openFile()` itself performs no path validation either — it simply does `shell.openExternal(\`file://${fullPath}\`)` [9](#0-8) .

The existing test suite proves the maintainers are aware that symlinks are the exact bypass vector for naive joins (`resolveWithin` has dedicated symlink-escape tests) [10](#0-9) , yet that same protection was never applied uniformly to the file-action context menus, only to two narrower code paths.

## Impact Explanation
An attacker who controls a cloned/fetched repository, or a fork whose branch is being reviewed via the "Open Pull Request" file list, can commit a symlink that resolves outside the working directory to a sensitive file (e.g., `~/.ssh/id_rsa`, `~/.aws/credentials`, `~/Library/Keychains/*`, or a browser cookie/profile store) or to an existing executable. A victim who inspects the (attacker-named, seemingly benign) file in the Changes tab, History tab, or PR file list and chooses "Reveal in Finder/Explorer" will have the OS file manager open on the directory containing the sensitive target file, disclosing its location/contents in the UI; choosing "Open with default program" or the configured external editor opens that arbitrary target file's contents directly, which can leak credentials, private keys, or other private data outside repository boundaries — the "read outside the repo" impact category. This does not require local/physical access, prior malware, or leaked credentials; only that the user open/browse an attacker-authored repository or PR and click a context-menu action that already exists in normal workflow.

## Likelihood Explanation
Moderate-to-high. No unusual user action is required beyond routine repository review (viewing changed files and using "Reveal in Finder"/"Open with default program", which are advertised, first-class context-menu items) [11](#0-10) . The PR file-list variant is especially concerning because it lets an attacker offer this via a pull request without the victim ever pulling/checking out the branch into their own working tree in the traditional sense — reviewers routinely browse "Files changed" for unfamiliar PRs. Git natively supports tracked symlinks on macOS/Linux (Windows symlink support in git is more limited, reducing likelihood there), so the attack is straightforward to construct with an ordinary `git add`/commit of a symlink.

## Recommendation
Route every file path derived from repository content (working directory changes, committed file changes, PR files-changed, conflict resolution overflow menu) through `resolveWithin(repository.path, path)` before it is passed to `revealInFileManager`, `openFile`, or an external editor launcher, mirroring the pattern already used in `dispatcher.ts`'s `openRepositoryFromUrl` and in `copilot-conflict-context.ts`. If `resolveWithin` returns `null`, refuse the action and surface an error (e.g., "This file cannot be opened because it resolves outside the repository") rather than silently falling through to a naive `Path.join`.

## Proof of Concept
1. Attacker creates a repository/branch containing a tracked symlink, e.g.:
   ```
   ln -s ../../../../../home/victim/.ssh/id_rsa notes.txt
   git add notes.txt
   git commit -m "add notes"
   ```
2. Victim clones the repo (or opens the attacker's fork/PR in GitHub Desktop's "Open Pull Request" file list) and views the Changes/History/PR-files list; `notes.txt` appears as an ordinary changed file.
3. Victim right-clicks `notes.txt` and selects "Reveal in Finder"/"Show in Explorer" or "Open with default program".
4. The app computes `Path.join(repository.path, 'notes.txt')` — this still equals `.../repo/notes.txt` on the path-string level, so no check rejects it — and passes it to `shell.showItemInFolder` / `shell.openExternal('file://...')`, which follow the symlink and reveal/open `/home/victim/.ssh/id_rsa`, disclosing the private key file's location and contents to the attacker's crafted UI flow (or, if the victim saves/copies content believing it's the tracked notes file, exfiltrating secrets inadvertently). [4](#0-3) [5](#0-4) [7](#0-6) [1](#0-0)

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

**File:** app/src/ui/changes/sidebar.tsx (L282-285)
```typescript
  private onOpenItem = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    openFile(fullPath, this.props.dispatcher)
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L185-200)
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

**File:** app/src/ui/lib/open-file.ts (L4-17)
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

**File:** docs/process/testing.md (L317-320)
```markdown
        - [ ] User can ignore single/all files, show in Finder/Explorer, reveal in external editor, or open in default program
	  - [ ] A specific file can only be ignored once
	  - [ ] All ignored files found in Repository Settings > Ignored Files tab
	- [ ] User can open in finder, preferred editor, or OS default program  
```
