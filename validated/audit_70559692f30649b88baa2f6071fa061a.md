### Title
Symlink-based path-traversal escape in `revealInFileManager()` / raw `Path.join(repository.path, file.path)` call sites bypasses the app's own `resolveWithin()` guard - (File: app/src/lib/app-shell.ts)

### Summary
GitHub Desktop has a dedicated, well-tested path-safety primitive, `resolveWithin()`, that resolves a relative path against a root and rejects the result if it (after `realpath`) escapes the root — explicitly to defend against symlink-based directory-traversal (`app/src/lib/path.ts:36-72`, with a unit test for exactly this case at `app/test/unit/path-test.ts:65-78`). Some call sites that convert an attacker-influenced, repo-relative file path into a full filesystem path *do* use this guard (`app/src/ui/dispatcher/dispatcher.ts:1957-1971`, `app/src/lib/copilot-conflict-context.ts:390-407`), but several others that also consume file paths coming from a cloned/fetched repository's Git status/diff/conflict data instead perform a bare `Path.join(repository.path, path)` with no traversal or symlink check at all: `revealInFileManager()` in `app/src/lib/app-shell.ts:61-63`, the "Open in external editor" / "reveal" handlers in `app/src/ui/lib/conflicts/unmerged-file.tsx:94`, `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx:214`, and `app/src/ui/open-pull-request/pull-request-files-changed.tsx:162`.

### Finding Description
This is the same broken-invariant shape as the audited `_supplyPool()` bug: one boundary check exists (the vault's own cap / here, `resolveWithin`'s traversal+symlink guard), but it is not applied uniformly to every code path that needs it, so a value that should always be validated (a pool cap / here, a repo-relative file path) reaches a sensitive operation unchecked through an alternate path.

Concretely, `revealInFileManager()` is explicitly documented in `app/src/lib/app-shell.ts:16-19` ("Do not use this method with non-validated paths") yet its own implementation, and the callers that feed it, never validate the path:

```
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
}
``` [1](#0-0) 

`path` here (and in the sibling "open in external editor" callers) ultimately originates from Git-tracked file paths surfaced through the UI — e.g. conflicted-file entries rendered in `renderUnmergedFile`/`renderConflictedFileWithConflictMarkers` (`app/src/ui/lib/conflicts/unmerged-file.tsx:33-131,245-334`), pull-request changed-file entries in `pull-request-files-changed.tsx:154-225`, and Copilot conflict-resolution entries in `copilot-conflicts-dialog.tsx:189-236`. All three sites do `join(repository.path, path)` (or `Path.join`) directly, with none of the traversal-through-symlink or null-byte checks that `resolveWithin` performs.

Git allows a repository to track a symlink as a regular blob (mode `120000`). A malicious/untrusted repository (delivered via clone, fetch, a shared branch, a PR, or an "Open in Desktop" deep link) can commit a tracked symlink such as `evil -> ../../../../` alongside a file that appears to live under `evil/…`, or introduce this configuration purely inside conflict metadata (unmerged/renamed paths) that the UI trusts and joins without resolving the real filesystem target. When the user then right-clicks that entry and chooses "Reveal in File Manager" / "Open in External Editor" / "Open with Default Program", `Path.join` happily concatenates the strings, but because a real symlink sits on disk at that intermediate path component, the OS actually resolves the final path outside the repository (`resolveWithin`'s own test demonstrates this exact class of escape at `app/test/unit/path-test.ts:65-78`).

### Impact Explanation
The unvalidated join lets an attacker-controlled repository redirect a user-triggered "reveal"/"open" action to an arbitrary filesystem location outside the intended repository directory. Depending on target OS/file manager behavior and what file exists there, this can expose the location or contents of sensitive files (e.g., surfacing `~/.ssh` in Finder/Explorer, or launching an external editor/"open with default program" against an arbitrary file) — i.e. reading/exposing content outside the repo without the user having asked for that location, purely by clicking a menu item the UI presented as "operating on this repo file." This matches the requested impact class (file read/open outside the repo boundary, via a crafted repository the attacker controls) even though it requires a user-initiated context-menu click rather than a fully silent trigger.

### Likelihood Explanation
Requires: (1) the victim clones/fetches/opens an attacker-supplied repository or PR containing a crafted symlink + path combination, and (2) the victim performs one context-menu action (reveal/open) on the affected entry — no admin rights, no local pre-existing malware, and no leaked credentials are needed. The precondition is fully attacker-controlled repository content, which is within the "attacker controls a cloned/fetched repository" scope explicitly called out as valid. However, it does require a specific plausible-looking crafted path/symlink to be created and a user click, which lowers likelihood relative to a fully silent, zero-click bug.

### Recommendation
Route every repo-relative-path-to-filesystem-path conversion that feeds a shell/file-manager/editor launch through the existing `resolveWithin()` helper (as already done in `dispatcher.ts` and `copilot-conflict-context.ts`), rejecting (and logging) any path that resolves outside the repository root, instead of using bare `Path.join`/`join`. At minimum, fix `revealInFileManager()` in `app/src/lib/app-shell.ts` since it is the single documented "do not use with non-validated paths" chokepoint that all of these UI call sites funnel through, and audit `unmerged-file.tsx`, `copilot-conflicts-dialog.tsx`, and `pull-request-files-changed.tsx` for the same missing guard.

### Proof of Concept
Conceptual PoC (not executed, based on `resolveWithin`'s own symlink test at `app/test/unit/path-test.ts:65-78`):
1. Attacker creates a repository containing a tracked symlink entry `evil` pointing to `../../../../` (or another path traversing outside the working directory) and a nested path reference `evil/secret.txt` surfaced in a merge-conflict / PR diff / status entry.
2. Victim clones or opens this repository in Desktop and encounters the conflicted/changed file listed as `evil/secret.txt`.
3. Victim right-clicks the entry and selects "Reveal in File Manager" (or "Open in External Editor" / "Open with Default Program").
4. `revealInFileManager(repository, 'evil/secret.txt')` computes `Path.join(repository.path, 'evil/secret.txt')` — a string that, when the OS resolves the `evil` symlink component, actually points outside `repository.path`, causing the file manager/editor to open the attacker-chosen external location instead of a file inside the repo.

### Citations

**File:** app/src/lib/app-shell.ts (L61-63)
```typescript
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
```
