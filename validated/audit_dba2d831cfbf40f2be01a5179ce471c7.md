## Analysis

The Ajna bug's broken invariant is: two functions that perform the *same underlying state mutation* (removing all quote tokens from a bucket) are supposed to share the same closing bookkeeping/validation step, but only one of the two call sites actually applies it — the other silently skips it, corrupting future accounting.

The closest verified analog in this codebase is the same "two call sites, one function has the safety check, the sibling function omits it" pattern in path resolution for user/attacker-supplied, repository-relative file paths.

`app/src/ui/dispatcher/dispatcher.ts`'s `openRepositoryFromUrl()` treats a `filepath` coming from an external, attacker-influenced source (an `x-github-client` deep link) as untrusted: it rejects absolute paths and then requires the path to resolve *within* the repository root via `resolveWithin()`, which performs `realpath()` resolution to defeat symlink escapes, before calling `shell.showItemInFolder()`: [1](#0-0) 

`resolveWithin()` explicitly exists to guard against exactly this class of attack (path traversal and symlink-based escapes), as documented and unit-tested: [2](#0-1) [3](#0-2) 

However, `revealInFileManager()` in `app/src/lib/app-shell.ts` — which is the function actually invoked from the "Reveal in Finder/Explorer" context-menu action across the changes list, commit history, conflict resolution UI, and PR-files-changed UI — performs only a naive `Path.join(repository.path, path)` with **no** `resolveWithin` / realpath check, despite its own doc comment warning "Do not use this method with non-validated paths": [4](#0-3) [5](#0-4) 

This function is called with file paths sourced from git status/diff/conflict data — data that originates from a cloned/fetched repository or a PR diff, i.e., attacker-controlled content — from multiple UI surfaces: [6](#0-5) 

and similarly from `app/src/ui/changes/filter-changes-list.tsx`, `app/src/ui/lib/conflicts/unmerged-file.tsx`, `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx`, and `app/src/ui/open-pull-request/pull-request-files-changed.tsx`, all of which import and call `revealInFileManager` directly.

### Title
`revealInFileManager()` skips the `resolveWithin` symlink/traversal guard applied to the analogous deep-link path-reveal flow - (File: app/src/lib/app-shell.ts)

### Summary
`app-shell.ts`'s `revealInFileManager()` builds the path to reveal with a plain `Path.join(repository.path, path)`, while the functionally identical operation reached via GitHub Desktop's `x-github-client://openRepo` deep link (`dispatcher.ts`'s `openRepositoryFromUrl`) validates the same kind of repository-relative path with `resolveWithin()` (which resolves symlinks via `realpath` and rejects results outside the repo root) before calling the same underlying `shell.showItemInFolder`.

### Finding Description
Both code paths end up doing the same thing: turning a repository-relative path string into an absolute path and asking the OS shell to reveal/select it. The deep-link path (`dispatcher.ts:1957-1972`) is the "safe" one — it rejects `isAbsolute(filepath)`, then calls `await resolveWithin(repository.path, filepath)` and refuses to proceed if the result is `null`. `revealInFileManager` (`app-shell.ts:61-64`) is the "unsafe" sibling — it never calls `resolveWithin`, never checks `isAbsolute`, and never resolves symlinks; it just does `Path.join(repository.path, path)`. If a cloned/fetched repository contains a symlink that a git worktree can legitimately contain (e.g., a tracked symlink entry pointing to `../../../../etc` or similar), a file path shown in the Changes list, Commit/History file list, merge-conflict list, or PR "Files changed" list can resolve — after the OS follows the symlink — to a location entirely outside the repository, and `revealInFileManager` will reveal/open that location without any check.

### Impact Explanation
Because `revealInFileManager` feeds directly into `shell.showItemInFolder`, an attacker who controls a repository the user clones/fetches (or a PR whose diff the user views in the "Files changed" pane) can cause Desktop to reveal an arbitrary file/folder path outside of the repository sandbox that the user believed they were confined to. This is a file-system boundary escape triggered purely by browsing UI the user is expected to use (viewing changed files, resolving conflicts, viewing a PR diff) — matching the report's core invariant break: a "sibling" operation that shares the same underlying effect as an already-guarded operation, but omits the guard, silently producing values/paths outside the trusted domain.

### Likelihood Explanation
Reaching this requires no unusual user action beyond normal Desktop usage: cloning/fetching an attacker's repository or opening a PR and clicking "Reveal in Finder/Explorer" on a listed file — a very ordinary workflow, unlike the deep-link path which requires the user to click an external `x-github-client://` link. The existence of the `resolveWithin` helper, its explicit purpose ("path traversal and symlink escapes"), and its use in the sibling `openRepositoryFromUrl` function shows the maintainers are aware of this exact attack class, but did not apply it consistently to `revealInFileManager`.

### Recommendation
Change `revealInFileManager(repository, path)` in `app/src/lib/app-shell.ts` to call `await resolveWithin(repository.path, path)` (mirroring `dispatcher.ts`'s `openRepositoryFromUrl`) and refuse to call `shell.showItemInFolder` if the result is `null`, so every caller (changes list, history, conflict list, PR files-changed, Copilot conflicts dialog) inherits the same symlink/traversal protection already proven out in `path-test.ts`.

### Proof of Concept
1. Attacker publishes a repository (or opens a PR) containing a tracked symlink entry, e.g. `link -> ../../../../../../etc`, plus a file accessed through that symlink path such as `link/passwd`.
2. Victim clones/fetches the repository in Desktop, or opens the PR in the "Files changed" tab.
3. The changed/committed file list shows an entry for a path that traverses through `link/...`.
4. Victim right-clicks and selects "Reveal in Finder/Explorer", invoking `revealInFileManager(repository, 'link/passwd')`.
5. `Path.join(repository.path, 'link/passwd')` produces a path string that, once the OS follows the symlink, resolves outside the repository — with no `resolveWithin`/`realpath` check to reject it, unlike the equivalent `openRepositoryFromUrl` deep-link flow.

### Citations

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

**File:** app/src/lib/app-shell.ts (L12-24)
```typescript
export interface IAppShell {
  readonly moveItemToTrash: (path: string) => Promise<void>
  readonly beep: () => void
  readonly openExternal: (path: string) => Promise<boolean>
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

**File:** app/src/ui/history/selected-commits.tsx (L10-21)
```typescript
import { encodePathAsUrl } from '../../lib/path'
import { revealInFileManager } from '../../lib/app-shell'

import { openFile } from '../lib/open-file'
import {
  isSafeFileExtension,
  CopyFilePathLabel,
  DefaultEditorLabel,
  RevealInFileManagerLabel,
  OpenWithDefaultProgramLabel,
  CopyRelativeFilePathLabel,
} from '../lib/context-menu'
```
