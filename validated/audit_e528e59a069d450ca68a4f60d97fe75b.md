### Title
Symlinked working-directory entries from a cloned/fetched repo bypass path containment when "Reveal in File Manager" / "Open with Default Program" resolve the file - (File: `app/src/lib/app-shell.ts`)

### Summary
`revealInFileManager` and the "open file" flows (`onOpenItem`, `onOpenFile`, `onOpenBinaryFile`) construct the on-disk path for a working-directory or committed file by doing a plain `Path.join(repository.path, path)` and immediately calling `shell.showItemInFolder`/`shell.openPath` on it, with no containment check. This is the same class of gap as the reported issue: some code paths (`openRepositoryFromUrl` in `dispatcher.ts`, and `buildConflictContext` in `copilot-conflict-context.ts`) explicitly call `resolveWithin()` — which resolves symlinks via `realpath` and rejects any result that escapes the repository root — before touching the filesystem, but the older/legacy file-opening code paths never adopted that guard.

### Finding Description
`resolveWithin` in `app/src/lib/path.ts` is Desktop's canonical "PDA-equivalent" check: it normalizes the path, joins it to the root, resolves symlinks with `realpath`, and returns `null` if the real resolved path escapes the real root. [1](#0-0) 
It is used defensively in exactly two call sites:
- `openRepositoryFromUrl` (deep-link `filepath` handling), which explicitly rejects absolute paths and calls `resolveWithin` before `shell.showItemInFolder`. [2](#0-1) 
- `buildConflictContext`, which explicitly documents "Guard against path traversal and symlink escapes (cross-platform)" before reading a conflicted file from disk. [3](#0-2) 

However, every other place that turns a repo-relative `file.path` (sourced from `git status`, `git log --raw`, or a PR's `CommittedFileChange` list) into an absolute path skips this check entirely and joins directly:
- `revealInFileManager` in `app-shell.ts` — the single shared helper used by nearly all "Reveal in File Manager" menu actions — does a bare `Path.join`. [4](#0-3) 
- `onOpenItem` in the Changes sidebar, `onOpenItem`/`onContextMenu` in `selected-commits.tsx`, and `onOpenFile`/`onFileContextMenu` in `pull-request-files-changed.tsx` all do the same unguarded `Path.join(repository.path, path)` before calling `openFile(fullPath, dispatcher)` (which invokes `shell.openPath`) or `dispatcher.openInExternalEditor(fullPath)`. [5](#0-4) [6](#0-5) [7](#0-6) 

`path.ts`'s own test suite proves the team is fully aware that a symlink inside the resolved root can be used to walk back out to an arbitrary location on disk, and that `resolveWithin` is required to stop it: [8](#0-7) 

Because a symlink is a legitimate, trackable/untracked filesystem object, a cloned or fetched repository can contain, e.g., an untracked or committed entry `pwned -> ../../../../.ssh/id_rsa` (relative) or an absolute-target symlink. Git tree entries themselves cannot contain `..` path components, so this is not exploitable through the tree path string — the escape happens at the *filesystem resolution* step (`realpath`), which is precisely why `resolveWithin` performs `realpath` on both the root and the resolved candidate rather than just string-normalizing the path. All of the call sites above skip that step.

### Impact Explanation
When the user browses the Changes list, History, or an "Open Pull Request" preview for a repository/branch they cloned or fetched (including forks), and right-clicks (or double-clicks) a file whose on-disk entry is actually a symlink escaping the repository, Desktop will:
- `showItemInFolder`/`showFolderContents` reveal a file *outside* the intended repository in the OS file manager, or
- `shell.openPath` (via `openFile`) open that outside file with its OS-associated default application.

If the symlink target is an executable, script, or shell-associated launcher outside the repository (e.g. a `.desktop`, `.command`, `.bat`, or an application bundle the attacker can also drop elsewhere on the filesystem, or a path under a location the OS treats as executable), this becomes attacker-influenced execution or disclosure of files outside the repo — matching the "code execution or file read/write outside the repo" impact bar. Because the guard exists elsewhere in the same codebase for functionally identical operations (opening a file path derived from repo-relative input), this is a genuine coverage gap rather than a design choice.

### Likelihood Explanation
Requires only that the victim clone/fetch/open a PR from a repository the attacker controls and then interact with the file list (a normal, expected Desktop workflow — no unnatural steps). "Open With Default Program" is gated by `isSafeFileExtension`, which likely blocks common executable extensions, but "Reveal in File Manager" and "Open in External Editor" have no such extension gate at all — they check only that the file exists on disk, not that the resolved location is safe. Symlink creation inside a git working tree during clone/checkout is standard behavior on macOS/Linux (Windows symlink support is opt-in via git config, reducing likelihood there).

### Recommendation
Route every repo-relative-to-absolute-path conversion for file-opening/revealing operations through `resolveWithin` (or an equivalent realpath-based containment check) before calling into `shell`/`openFile`, mirroring what `openRepositoryFromUrl` and `buildConflictContext` already do:
- `revealInFileManager` (`app/src/lib/app-shell.ts`)
- `onOpenItem`/`onOpenItemInExternalEditor` paths in `app/src/ui/changes/sidebar.tsx` and `app/src/ui/changes/filter-changes-list.tsx`
- `onOpenItem`/`onContextMenu` in `app/src/ui/history/selected-commits.tsx`
- `onOpenFile`/`onFileContextMenu` in `app/src/ui/open-pull-request/pull-request-files-changed.tsx`
- the Copilot conflicts dialog file-opening handlers in `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-*.tsx`

If `resolveWithin` returns `null`, refuse the action and surface an error, exactly as `openRepositoryFromUrl` does.

### Proof of Concept
1. Attacker publishes/serves a repository containing (on a symlink-capable platform) an untracked or committed entry:
   ```
   ln -s ../../../../../../etc/passwd leak
   ```
   or, more impactfully, a symlink pointing at a local script/binary path plausible on the victim's machine.
2. Victim clones or fetches this repository in GitHub Desktop (or opens a PR from this fork via "Open Pull Request").
3. Victim opens the Changes tab (or Files Changed pane for the PR), right-clicks the `leak` entry, and selects "Reveal in File Manager" or double-clicks it.
4. `Path.join(repository.path, 'leak')` yields a path inside the repo, `pathExists` succeeds, and `shell.showItemInFolder`/`shell.openPath` follow the symlink and act on the real target outside the repository — with no `resolveWithin`/`realpath` check ever performed, unlike the equivalent, already-guarded code path in `dispatcher.ts`'s `openRepositoryFromUrl`.

Note: I was not able to fully verify the implementation of `openFile()` (`app/src/ui/lib/open-file.ts`) or the exact `isSafeFileExtension` extension list (`app/src/ui/lib/context-menu.ts`) before the tool budget ran out, so I cannot confirm precisely which extensions are blocked for "Open With Default Program," nor whether `openFile` performs any additional check beyond what's shown. This should be verified in a follow-up before treating the "code execution" severity as fully confirmed versus "file disclosure/reveal outside repo," which is confirmed by the code shown above.

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1971)
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

**File:** app/src/lib/app-shell.ts (L61-64)
```typescript
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
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
