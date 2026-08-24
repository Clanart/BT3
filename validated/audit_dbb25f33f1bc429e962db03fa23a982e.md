### Title
Opening or revealing a changed file follows a repo-planted symlink outside the working directory, bypassing the app's own path-containment guard - ([File: app/src/ui/lib/open-file.ts])

### Summary
GitHub Desktop already has a dedicated safety primitive, `resolveWithin()`, that is specifically designed to stop a path from escaping a repository root **even via a symlink**, and it is used to guard other attacker-influenced paths (e.g. deep-link file opening, Copilot conflict-file reads). However, the much more common "double-click a file" / "Open with default program" / "Reveal in Finder" flows for committed and working-directory changes never call this guard — they simply do `Path.join(repository.path, file.path)` and hand the result straight to the OS.

### Finding Description
`resolveWithin()` in `app/src/lib/path.ts` resolves a repo-relative path and confirms, via `realpath()` on both the root and the resolved target, that the final location is actually inside the root, explicitly to defeat symlink-based escapes: [1](#0-0) 

This exact threat is unit-tested and confirmed to work as intended: [2](#0-1) 

The guard is correctly applied for deep-link file opening in the dispatcher: [3](#0-2) 

and for reading conflicted files for Copilot: [4](#0-3) 

But the equivalent, far more commonly triggered "open file"/"reveal in file manager" actions for a repository-tracked path (working-directory changes, committed-file diffs, pull-request diffs, Copilot conflict resolution) never route through `resolveWithin()`. They build the path with a plain `Path.join` and pass it directly to `shell.openExternal`: [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

The same unvalidated `fullPath` is also used for "Reveal in file manager" and "Copy file path" in the context menu: [9](#0-8) 

Because `Path.join` does not resolve symlinks, if a tracked path in a commit or the working tree traverses through a directory entry that is itself a symlink (e.g. a tracked/untracked entry `evil -> /home/victim/.ssh` with a listed change at `evil/id_rsa`), the resulting `fullPath` transparently follows the filesystem symlink outside the repository. None of the call sites above ever check that the resolved path stays under `repository.path`, unlike the dispatcher and Copilot code paths that do use `resolveWithin`.

The only mitigating check present in some of these flows is an extension allow-list, `isSafeFileExtension`, used to enable/disable "Open with default program" in the context menu: [10](#0-9) 
This filters by *file extension*, not by *path containment*, and it is not applied at all to double-click open (`onRowDoubleClick`) or to "Reveal in file manager" — so even a benign extension can be used to open/reveal an out-of-repo target.

### Impact Explanation
An attacker who controls a cloned/fetched repository (a commit, or content that ends up in the working directory, e.g. via merge/checkout) can plant a symlink that redirects normal "open" and "reveal" actions to any path readable by the victim's OS user — including credential files (`~/.ssh`, `~/.aws`, browser profile data) or other sensitive locations. Double-clicking a file in the Changes list, History view, or a PR diff, or using "Open with default program"/"Reveal in file manager", triggers `shell.openExternal(file://...)` or the OS file-manager reveal call with a path outside the intended repository boundary — silently exposing (opening/revealing) files the user never intended to touch when interacting with what looks like a normal repository file.

### Likelihood Explanation
Double-clicking a changed/committed file to preview or open it is one of the most routine actions in GitHub Desktop's UI, and no unusual steps are required — the victim simply needs to clone/fetch the malicious repository and interact with a file it lists as changed. Git and the filesystem do allow symlinked directory components to exist in a working tree, and GitHub Desktop's own code (`resolveWithin`, its tests, and its use in `dispatcher.ts`/`copilot-conflict-context.ts`) shows the developers are aware this exact class of symlink escape is exploitable and worth guarding against for other paths — but the guard was never extended to the file-open/reveal code paths.

### Recommendation
Route every `fullPath = Path.join(repository.path, file.path)` construction that feeds `openFile`, `revealInFileManager`, or clipboard/"view" actions through `resolveWithin(repository.path, file.path)` (as already done in `dispatcher.ts` and `copilot-conflict-context.ts`), and refuse to open/reveal the file if the resolved path is `null`. This should be applied uniformly across `app/src/ui/changes/sidebar.tsx`, `app/src/ui/history/selected-commits.tsx`, `app/src/ui/open-pull-request/pull-request-files-changed.tsx`, `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx`, and `app/src/ui/repository.tsx`.

### Proof of Concept
1. Attacker creates a repository containing a tracked symlink `evil -> ../../../../home/victim/.ssh` (or an OS-appropriate equivalent target) and a file recorded at path `evil/id_rsa`.
2. Victim clones/fetches this repository in GitHub Desktop and views the commit/history (or checks it out so it appears in Changes).
3. Victim double-clicks the `evil/id_rsa` entry (or uses "Open with default program"/"Reveal in file manager" from the context menu).
4. `Path.join(repository.path, "evil/id_rsa")` is computed and passed unchecked to `openFile()`/`revealInFileManager()`; the OS follows the `evil` symlink, and the victim's default handler opens (or Explorer/Finder reveals) `~/.ssh/id_rsa`, a location entirely outside the cloned repository — demonstrating the file-open path never verifies containment the way `resolveWithin` does elsewhere in the same codebase.

### Citations

**File:** app/src/lib/path.ts (L64-71)
```typescript
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

**File:** app/src/ui/changes/sidebar.tsx (L282-285)
```typescript
  private onOpenItem = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/history/selected-commits.tsx (L292-295)
```typescript
  private onOpenItem = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/history/selected-commits.tsx (L384-428)
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
      { type: 'separator' },
      {
        label: CopyFilePathLabel,
        action: () => clipboard.writeText(fullPath),
      },
      {
        label: CopyRelativeFilePathLabel,
        action: () => clipboard.writeText(Path.normalize(file.path)),
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
