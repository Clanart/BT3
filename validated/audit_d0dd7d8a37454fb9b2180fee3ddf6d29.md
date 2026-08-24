Confirmed: `revealInFileManager` at [1](#0-0)  does a raw `Path.join(repository.path, path)` with no traversal/symlink guard, and its own JSDoc explicitly warns "Do not use this method with non-validated paths" — yet `copilot-conflicts-dialog.tsx` calls it (and `openFile`) with an unvalidated, repository-controlled path.

### Title
Copilot conflict-resolution dialog opens/reveals attacker-controlled repository paths without the `resolveWithin` traversal/symlink guard used elsewhere in the same feature - ([File: app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx])

### Summary
The Copilot merge-conflict dialog's "File options" overflow menu builds a file path with a plain `Path.join(repository.path, path)` and passes it to `openFile`, `openFileInExternalEditor`, and `revealInFileManager`. All three of these sinks operate on the raw path with no traversal or symlink validation, even though `app-shell.ts` explicitly documents that these APIs must not be called with non‑validated paths. The sibling code path in the very same feature — writing Copilot's resolved content to disk in `app-store.ts` — validates the identical `path` value through `resolveWithin` before touching the filesystem. This is the same asymmetry pattern as the external report: one direction of a shared value is checked, the other is not.

### Finding Description
`onOverflowMenuClick` in the dialog takes `path` (a conflicted file's repository-relative path, sourced from `WorkingDirectoryFileChange`/`copilotResolutions`, which in turn comes from parsed `git status` / merge-conflict state during a merge, rebase, or cherry-pick against attacker-supplied history) and does: [2](#0-1) 

```
private onOverflowMenuClick = (path: string) => {
  const { repository, dispatcher, resolvedExternalEditor } = this.props
  const absolutePath = join(repository.path, path)
  ...
  { label: OpenWithDefaultProgramLabel, action: () => openFile(absolutePath, dispatcher) },
  { label: RevealInFileManagerLabel, action: () => revealInFileManager(repository, path) }
```

Both `openFile` (`shell.openExternal('file://...')`) and `revealInFileManager` (`Path.join` + `shell.showItemInFolder`) resolve the path with no `realpath`/root-containment check: [3](#0-2) [4](#0-3) 

Compare this to the write path for the exact same untrusted `path`/`resolution.path` value, in the code that actually writes Copilot's resolved content to disk for this same dialog's "Continue" action: [5](#0-4) 

```
const absolutePath = await resolveWithin(repository.path, resolution.path)
if (absolutePath === null) {
  log.warn(`Copilot resolution skipped: path outside repository: ${resolution.path}`)
  continue
}
```

`resolveWithin` (`app/src/lib/path.ts:36-71`) normalizes, rejects null bytes, and — critically — calls `realpath` on both the root and the resolved path so that a tracked git symlink cannot be used to escape the repository root: [6](#0-5) 

The overflow-menu code path skips all of this. Because git supports storing symlinks as first-class tree entries, an attacker who controls a branch/fork that the victim merges, rebases, or cherry-picks (a normal, unprivileged, "attacker controls a cloned/fetched repository" scenario) can commit a conflicting file that is actually a symlink pointing outside the working directory (e.g. to `~/.ssh/id_rsa`, a shell profile, or an executable). When Copilot flags that path as conflicted/skipped and the user clicks the file's overflow menu → "Open with default program" or "Reveal in File Manager" (a normal one-click action a user is expected to take while resolving conflicts), Desktop follows the symlink outside the repository, causing the OS to open/reveal a file the attacker chose, entirely outside the intended repo sandbox. This directly violates the `resolveWithin` invariant the app already established for this exact `path` value elsewhere in the same flow.

### Impact Explanation
This lets a malicious repository owner cause GitHub Desktop to open or reveal an attacker-chosen path outside the cloned repository via a single click during normal conflict-resolution UI. Depending on OS file-association handling this can disclose sensitive local file contents (`openFile`/`openFileInExternalEditor` handing a credential file to the default viewer/editor) or, where the target is a script/executable with an OS-level "open" association that executes it, achieve code execution outside the repository — matching the report's "attacker controls a cloned/fetched repository ... resulting in code execution [or] file read outside the repo" category.

### Likelihood Explanation
Requires no privileges beyond the victim adding/fetching a malicious remote and hitting a merge/rebase/cherry-pick conflict that Copilot's conflict-resolution flow surfaces (a mainstream Desktop workflow), then clicking a normal overflow-menu action on the flagged file. Git natively supports tracked symlinks, so crafting the malicious tree entry is straightforward and requires no exploit of git internals — only a slightly unusual (but fully valid) repository.

### Recommendation
Route `path` through `resolveWithin(repository.path, path)` (and reject on `null`) before calling `openFile`, `openFileInExternalEditor`, or `revealInFileManager` in `onOverflowMenuClick`, mirroring the guard already applied in `AppStore.applyCopilotConflictResolutions`. Consider hardening `revealInFileManager`/`openFile`/`app-shell.ts` themselves to require a pre-validated absolute path (their docstrings already assume this) or to perform the `resolveWithin` check internally so future callers cannot silently bypass it.

### Proof of Concept
1. Attacker creates a repo/branch where a merge with the victim's branch produces a conflict on a path, e.g. `notes.txt`, but the version on the attacker's side is committed as a symlink (`git add notes.txt` after `ln -s /Users/victim/.ssh/id_rsa notes.txt`, or `~/Library/...`/Windows equivalent).
2. Victim adds the attacker's remote, fetches, and merges/rebases, hitting a conflict; Copilot's conflict-resolution dialog lists `notes.txt` (as conflicted or skipped).
3. Victim clicks the file's overflow menu (kebab icon) → "Open with default program" or "Reveal in File Manager".
4. `onOverflowMenuClick` builds `Path.join(repository.path, 'notes.txt')`, which the OS resolves through the symlink to `/Users/victim/.ssh/id_rsa`, and `openFile`/`revealInFileManager` open/reveal that file — with no `resolveWithin` check ever performed, unlike the write path in `app-store.ts:7233-7239`.

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

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }
```

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```
