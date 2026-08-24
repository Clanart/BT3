Based on the codebase evidence, I found a solid analog for this bug class: a security invariant ("resolved conflict-file paths must stay within the repository") is explicitly implemented in one code path of the Copilot conflict-resolution feature, but the sibling UI action in the very same dialog resolves the identical attacker-influenced path without that check.

### Title
Missing repo-boundary/symlink validation when opening Copilot conflict files from the overflow menu allows reading/executing files outside the repository - (File: `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx`)

### Summary
`buildConflictContext` in `app/src/lib/copilot-conflict-context.ts` explicitly guards conflicted-file paths against path traversal and symlink escapes before reading file content, using `resolveWithin` (which resolves symlinks via `realpath` and confirms containment) [1](#0-0) . The comment even states the intent: "Guard against path traversal and symlink escapes (cross-platform)" [2](#0-1) . However, the "overflow menu" action in the Copilot Conflicts dialog — which lets the user open the *same* conflicted file path in an external editor, the OS default program, or the file manager — builds the absolute path with a plain `join(repository.path, path)` and never validates it stays inside the repository: [3](#0-2) .

### Finding Description
The broken invariant is: "any path derived from an unmerged/conflicted file entry must be confirmed, after symlink resolution, to reside inside `repository.path` before it is used for any file I/O or process-launch action." This invariant is enforced for the content read into the Copilot conflict-resolution prompt (`buildConflictContext`), but not for the equivalent user-facing "Open with Default Program" / "Open in `<editor>`" / "Reveal in File Manager" actions triggered from `onOverflowMenuClick` in `copilot-conflicts-dialog.tsx`. The `path` value passed into `onOverflowMenuClick` comes from the working directory's list of unmerged/conflicted files (`getUnmergedFiles`), which is derived from git status output for a cloned/fetched repository — attacker-controlled content. If a hostile repository contains, or a merge/rebase introduces, a path component that is a symlink pointing outside the working directory (e.g. `evil-dir -> /` or `evil-dir -> ~/.ssh`), then a conflicted file reported as `evil-dir/config` will resolve on disk to a location entirely outside the repository once `Path.join` is used, because `join` performs no filesystem resolution and no containment check.

`revealInFileManager` (`app/src/lib/app-shell.ts`) similarly does a raw `Path.join` with an explicit doc-comment warning "Do not use this method with non-validated paths" [4](#0-3)  — yet the Copilot conflicts dialog calls it with the raw conflicted-file path.

### Impact Explanation
Clicking "Open with Default Program" or "Open in `<editor>`" from the overflow menu launches `shell.openExternal`/the external editor process pointed at the symlink-resolved absolute path (`openFile` in `app/src/ui/lib/open-file.ts`, `launchExternalEditor`/`launchCustomExternalEditor` in `app/src/lib/editors/launch.ts`). This can expose or execute arbitrary files/programs outside the repository that the victim's OS user has access to (e.g. opening `~/.ssh/id_rsa` in a text editor, or worse, if the symlink points at an executable and the associated default-program handler executes it). This satisfies the "file write or read outside the repo" / "code execution" impact category from an attacker-controlled cloned/fetched repository plus a link the user clicks in the UI (the overflow-menu item), matching the valid-impact criteria.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to (a) open a hostile repository that has a merge/rebase conflict whose path traverses a symlinked directory, and (b) explicitly invoke the Copilot conflict resolution flow and click the per-file overflow menu ("Open in editor" / "Open with default program" / "Reveal in File Manager") rather than accepting Copilot's automatic resolution. This is a normal, expected user action within the feature's own UI (not an "unnatural" step), and the codebase's own tests (`app/test/unit/path-test.ts`) demonstrate that this exact symlink-escape technique is a recognized, exploitable primitive against unguarded `Path.join` usage [5](#0-4) .

### Recommendation
In `onOverflowMenuClick` (and any other Copilot-conflict UI path that turns a conflicted-file's relative path into an absolute path — including the `revealInFileManager` call in the same handler), replace the raw `join(repository.path, path)` with `resolveWithin(repository.path, path)` (already used in `buildConflictContext`) and refuse to open/reveal the file if it returns `null`, consistent with the guard that already exists for the content sent to Copilot.

### Proof of Concept
1. Clone/open a repository (attacker-controlled) that contains a symlink `evil -> /` (or another sensitive absolute path) committed as a tracked entry, and craft a merge/rebase so that git reports a conflicted file at `evil/some-file`.
2. Trigger Copilot's automated conflict resolution (`MultiCommitOperationStepKind.ShowCopilotConflicts`), open the resulting dialog.
3. For the file listed as `evil/some-file`, click the overflow menu and choose "Open with Default Program" or "Open in `<editor>`".
4. `onOverflowMenuClick` computes `absolutePath = join(repository.path, 'evil/some-file')` [6](#0-5)  — no `resolveWithin`/`realpath` check is performed, unlike the equivalent code path in `buildConflictContext` [7](#0-6)  — and the OS opens the file that the `evil` symlink actually points to, which is outside the repository.

### Citations

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
