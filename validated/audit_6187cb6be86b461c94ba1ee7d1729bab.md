### Title
`onFileContextMenu` builds file paths for a PR's changed files with plain `Path.join` instead of a `resolveWithin`-style containment check, allowing symlink-based escape from the repository - (File: `app/src/ui/open-pull-request/pull-request-files-changed.tsx`)

### Summary
`PullRequestFilesChanged.onFileContextMenu` and its helper `onOpenFile` both compute the on-disk path for a pull request's changed file using `Path.join(repository.path, file.path)`, where `file.path` comes from `CommittedFileChange` objects built from GitHub API pull-request file data. No `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` containment check (the pattern used elsewhere in the codebase, e.g. `app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/copilot-conflict-context.ts`) is applied before that path is used to check existence, reveal in file manager, open in an external editor, or open with the OS default program.

### Finding Description
`file.path` for a PR's Files Changed list is untrusted attacker-controlled data (a path string reported by the GitHub API for the PR diff). At `app/src/ui/open-pull-request/pull-request-files-changed.tsx:162`:

```ts
const fullPath = Path.join(repository.path, file.path)
``` [1](#0-0) 

and again in `onOpenFile`:
```ts
private onOpenFile = (path: string) => {
  const fullPath = Path.join(this.props.repository.path, path)
  this.onOpenBinaryFile(fullPath)
}
``` [2](#0-1) 

`Path.join` only normalizes `..`/`.` segments syntactically; it does not perform any filesystem-level resolution and cannot detect when a component of the joined path is a symlink that redirects the final resolved location outside `repository.path`. The codebase already has a dedicated utility for this exact class of problem, `resolveWithin` (and its POSIX/Win32 variants), defined in `app/src/lib/path.ts`, which resolves the joined path and then calls `fs.realpath` on both the root and the resolved path to verify the real, symlink-resolved path is still contained within the real root before returning it; otherwise it returns `null`. This utility is used elsewhere in the app (e.g. `app/src/ui/dispatcher/dispatcher.ts`, `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/stores/app-store.ts`) precisely to guard against this kind of path escape, but `pull-request-files-changed.tsx` does not use it for the PR-file-context-menu path.

Because `file.path` originates in the base/head branch content of the pull request (an attacker who opens a PR against the victim's repository fully controls the tree, including committing a symlinked directory component such as `evil-link -> /` or `evil-link -> ~/.ssh`), a crafted PR file path like `evil-link/secret` will be joined with `repository.path` to a `fullPath` whose leading directory component, once fetched/checked out or already present in the working directory, is a symlink that redirects outside the repository root. Subsequent operations - `pathExists(fullPath)`, `revealInFileManager(repository, file.path)`, `dispatcher.openInExternalEditor(fullPath)`, and `openFile(fullPath, dispatcher)` (triggered by "Open With Default Program") - then act on a location outside the intended repository scope.

### Impact Explanation
If exploited, a user right-clicking a file in the "Files changed" tab of a PR opened from an untrusted contributor could have Desktop check for, reveal, or open (via the OS default handler or configured external editor) a file located outside the cloned repository, based solely on a symlink the attacker committed to the tracked tree. This can disclose the existence or contents of files outside the repo (e.g. via "Open With Default Program" launching an app that displays file contents) or open arbitrary attacker-chosen locations on disk, matching the "read/transmit files outside the selected repository scope" impact category.

### Likelihood Explanation
Exploitation requires: (1) the attacker to have a symlink already present in the repository's working directory pointing outside the repo (introduced via a base-branch commit, since a symlinked path component must exist on disk for `pathExists`/`realpath`-style resolution to actually escape), and (2) the local user to view that specific PR's Files Changed tab and explicitly right-click and choose "Open With Default Program" (or another context-menu action) on the affected file entry. This requires the victim to fetch/checkout the base branch content containing the symlink and to interact with a specific PR file - a real but non-trivial condition, since some repository state and explicit user interaction are needed, though no special privileges are required beyond opening a PR against the repo.

### Recommendation
Replace the direct `Path.join(repository.path, file.path)` calls in `onFileContextMenu` and `onOpenFile` in `app/src/ui/open-pull-request/pull-request-files-changed.tsx` with `resolveWithin(repository.path, file.path)` (as used in `app/src/ui/dispatcher/dispatcher.ts` and `app/src/lib/copilot-conflict-context.ts`), and treat a `null` result the same as "file does not exist on disk" (disable all path-dependent menu items and skip opening/revealing/copying the path).

### Proof of Concept
1. As an attacker, create a fork/branch and commit a symlink at the repository root, e.g. `escape -> ../../../../` (pointing outside the repo working directory), then add a file that appears under that path in the PR diff, e.g. `escape/secret.txt`.
2. Open a pull request from this branch against the target repository.
3. In GitHub Desktop, open the PR's "Files changed" tab; the victim's local working copy of the base branch must contain the `escape` symlink (e.g., after fetching/checking out that ref).
4. Right-click the `escape/secret.txt` entry and choose "Open With Default Program".
5. Observe that `fullPath = Path.join(repository.path, 'escape/secret.txt')` resolves through the `escape` symlink to a location outside the repository, and Desktop opens/reads that external path instead of refusing due to containment, because no `resolveWithin` check is applied at `app/src/ui/open-pull-request/pull-request-files-changed.tsx:162`.

### Citations

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L86-89)
```typescript
  private onOpenFile = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    this.onOpenBinaryFile(fullPath)
  }
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L160-163)
```typescript
    const { repository } = this.props

    const fullPath = Path.join(repository.path, file.path)
    const fileExistsOnDisk = await pathExists(fullPath)
```
