## Analysis

The Cosmos bug pattern is: two code paths perform conceptually the same operation, but only one of them enforces an invariant-preserving check that the other skips, so the missing check silently corrupts a protected value.

The same shape exists in GitHub Desktop's path-safety story. The app has a hardened primitive, `resolveWithin()` (`app/src/lib/path.ts`), that resolves a repo-relative path and verifies — via `realpath` on both the root and the resolved path — that the result doesn't escape the repository, explicitly to defend against symlink-based escapes. It's used by the newer/patched code paths, e.g. `buildConflictContext` in `copilot-conflict-context.ts` and `openRepositoryFromUrl` in `dispatcher.ts`, both of which reject the path if `resolveWithin` returns `null`.

But the much older, far more commonly exercised "open this file" primitives never adopted that guard: [1](#0-0) [2](#0-1) 

`revealInFileManager` does a plain `Path.join(repository.path, path)` and hands the result straight to `shell.showItemInFolder`, despite the interface's own doc comment on `showItemInFolder`/`openPath` stating "Do not use this method with non-validated paths." The same unguarded pattern repeats in `_openInExternalEditor`/`onOpenInExternalEditor` and `onOpenItem`: [3](#0-2) [4](#0-3) [5](#0-4) 

These are fed `file.path` values that originate from git tree/diff data for arbitrary commits — including commits from a cloned/fetched malicious repository or PR (`pull-request-files-changed.tsx`, `selected-commits.tsx`), not just the user's own working tree: [6](#0-5) [7](#0-6) 

Since git supports symlink blobs (mode `120000`), a malicious repository can commit a symlinked path component (e.g. a tracked "directory" entry that is actually a symlink to `/` or another sensitive location) plus a file underneath it. `Path.join` has no knowledge of on-disk symlinks, so `revealInFileManager`/`onOpenItem`/`_openInExternalEditor` will compute a path that traverses the symlink to a location outside the repository, and hand it to Electron's `shell.showItemInFolder`, `openPath`, or the configured external editor launcher — exactly the class of escape that `resolveWithin`'s `realpath` check exists to prevent, as its own test suite documents: [8](#0-7) 

### Title
Symlink-based path traversal when revealing/opening repository files bypasses `resolveWithin` safety net - (File: app/src/lib/app-shell.ts)

### Summary
`resolveWithin()` was introduced (and is used by `copilot-conflict-context.ts` and `dispatcher.ts`'s deep-link handler) specifically to stop symlink/traversal escapes when resolving a repo-relative path to an absolute one. `revealInFileManager`, `_openInExternalEditor`, and `onOpenItem`/`onOpenBinaryFile` never adopted this guard; they still resolve paths with a bare `Path.join(repository.path, path)`.

### Finding Description
`Path.join` performs pure string concatenation/normalization; it does not consult the filesystem. If any path component under the repository root is actually a symlink (which Git tracks natively via mode `120000` blobs), `Path.join` will produce a string that, when passed to the OS, resolves through the symlink to a location outside the repository — even though the string itself contains no `..` segments. `resolveWithin` closes this gap by calling `realpath` on both the root and the candidate path and verifying containment, but that fix is applied inconsistently: only in the Copilot conflict-resolution code and the `x-github-client://openLocalRepo` deep-link handler.

### Impact Explanation
An attacker who controls a cloned/fetched repository (or a PR the victim reviews in Desktop) can commit a symlinked directory pointing outside the repo plus a "file" underneath it. When the victim uses ordinary UI actions — Reveal in Finder/Explorer, Open in external editor, Open with default program, or the equivalent action from the PR "Files changed" or commit history views — Desktop computes a path that traverses the attacker-planted symlink and hands it to the OS shell or the configured editor, exposing or launching content from outside the intended repository sandbox.

### Likelihood Explanation
No unusual user behavior is required beyond normal repository interaction (viewing a PR/commit's changed files and using a standard context-menu action such as "Reveal in Finder" or "Open in external editor"), which are core, everyday Desktop workflows. The attacker only needs to get the victim to clone/fetch a repository or view a PR — well within the documented threat model.

### Recommendation
Route `revealInFileManager`, `_openInExternalEditor`/`launchExternalEditor`, `onOpenItem`, and `onOpenBinaryFile` through `resolveWithin` (or an equivalent `realpath`-based containment check) before calling `shell.showItemInFolder`/`openPath`/launching the editor, mirroring the pattern already used in `copilot-conflict-context.ts` and `dispatcher.ts`.

### Proof of Concept
1. Attacker creates a repository containing a tracked symlink entry, e.g. `evil -> /` (mode `120000`), and a tracked file `evil/etc/passwd` (or any interesting path under the symlink target).
2. Victim clones/fetches this repository, or reviews it as a PR, in GitHub Desktop.
3. In the Changes/History/"Files changed" view, victim right-clicks the file shown as `evil/etc/passwd` and selects "Reveal in Finder" (or "Open in external editor").
4. `revealInFileManager` computes `Path.join(repoPath, 'evil/etc/passwd')`; on disk this traverses the `evil` symlink to `/etc/passwd`, and `shell.showItemInFolder`/the editor operates on that out-of-repo path rather than something confined to the clone.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L7605-7626)
```typescript
  /** Open a path to a repository or file using the user's configured editor */
  public async _openInExternalEditor(fullPath: string): Promise<void> {
    const { selectedExternalEditor, useCustomEditor, customEditor } =
      this.getState()

    try {
      if (useCustomEditor && customEditor) {
        await launchCustomExternalEditor(fullPath, customEditor)
      } else {
        const match = await findEditorOrDefault(selectedExternalEditor)
        if (match === null) {
          this.emitError(
            new ExternalEditorError(
              `No suitable editors installed for GitHub Desktop to launch. Install ${suggestedExternalEditor.name} for your platform and restart GitHub Desktop to try again.`,
              { suggestDefaultEditor: true }
            )
          )
          return
        }

        await launchExternalEditor(fullPath, match)
      }
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L184-199)
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
