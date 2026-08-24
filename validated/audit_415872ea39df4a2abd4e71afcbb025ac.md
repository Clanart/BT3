### Title
Symlink/path-traversal escape in Copilot conflict-resolution diff via `getResolutionDiff()` reading files without `resolveWithin()` guard - (File: `app/src/lib/git/diff.ts`)

### Summary
`getResolutionDiff()` reads the "base" (working-tree) side of a conflict-resolution diff by joining the repository path with an attacker-influenceable relative `filePath` using plain `Path.join()`/`readFile()`, with no symlink or path-containment check. A sibling function that serves the same feature (`buildConflictContext()` in `app/src/lib/copilot-conflict-context.ts`) explicitly guards the identical operation with `resolveWithin()` to defend against "path traversal and symlink escapes." The absence of that guard in `getResolutionDiff()` is the analog of the `Goldilend` bug: a security-relevant step (path containment validation) that exists in one code path but is skipped/omitted in the sibling code path that performs the same underlying operation, allowing a stale/unchecked value (the resolved absolute path) to be used for a filesystem read.

### Finding Description
When a merge/rebase/cherry-pick has conflicts, `WorkingDirectoryFileChange.path` entries come from `git status`, which can include paths for files that are themselves symlinks tracked in the repository (created by any commit, including ones from a remote/fork the user merges or checks out). `buildConflictContext()` (`app/src/lib/copilot-conflict-context.ts:376-470`) recognized this risk and calls:

```
absolutePath = await resolveWithin(workingDirectory, file.path)
``` [1](#0-0) 

`resolveWithin` (`app/src/lib/path.ts:36-71`) resolves both the root and the target through `realpath()` and rejects the read unless the real, symlink-resolved path is still contained within the repository root: [2](#0-1) 

However, `getResolutionDiff()`, which is used by the same Copilot conflict-resolution feature to render the diff comparison in `copilot-conflicts-changes.tsx`, reads the identical "on-disk conflicted file" content with a raw join instead:

```
const baseContent = await readFile(
  Path.join(repository.path, filePath),
  'utf8'
)
``` [3](#0-2) 

There is no call to `resolveWithin`/`realpath` and no rejection of paths that traverse outside the repository via a symlinked directory or symlinked file entry. If a conflicted file path resolves (through a repo-committed symlink) to a location outside the working directory, `getResolutionDiff` will happily read that external file's contents and hand it back as `oldContents`/rendered diff text.

### Impact Explanation
This breaks the same invariant the code elsewhere explicitly protects: "never read filesystem content outside the repository root when acting on repo-supplied paths." A crafted upstream/fork branch that a user merges, rebases onto, or cherry-picks (a git object under attacker control) can introduce a tracked symlink at a conflicted path pointing to a sensitive file outside the repo (e.g., an SSH key, `.netrc`, or an OAuth/token file used elsewhere by Desktop). When the user opens the "Resolve Conflicts with Copilot" diff view for that path, `getResolutionDiff` reads and displays the target file's content in the diff UI — an out-of-repo file-read/disclosure triggered purely by the user opening a normal conflict-resolution dialog on attacker-supplied repository content. This matches the requested impact class: attacker controls a fetched/merged repository, result is file read outside the repo.

### Likelihood Explanation
Likelihood is moderate-to-high for anyone using the Copilot conflict resolution feature: no unusual user action is required beyond a normal merge/rebase/cherry-pick with conflicts and opening the resolution dialog, which is the intended, expected workflow once a conflict exists. The existence of an explicit, documented guard in the sibling `buildConflictContext` function (with an inline comment "Guard against path traversal and symlink escapes") shows the maintainers are aware of and already mitigate this exact risk elsewhere for this exact feature — confirming the missing check in `getResolutionDiff` is an inconsistency/omission rather than an accepted design tradeoff.

### Recommendation
In `getResolutionDiff()` (`app/src/lib/git/diff.ts`), resolve `filePath` through `resolveWithin(repository.path, filePath)` (the same helper `buildConflictContext` uses) before calling `readFile`, and bail out (returning an "unrenderable"/skipped result) if the resolved path is `null` or escapes the repository root. The same treatment should be applied anywhere else in the diff/conflict pipeline that joins `repository.path` with a repo-reported file path without going through `resolveWithin`.

### Proof of Concept
1. Attacker prepares a branch/fork containing a tracked symlink, e.g. `secret.txt -> /home/victim/.ssh/id_rsa`, at a path that will conflict with the victim's local changes to the same path.
2. Victim fetches/merges/rebases this branch in GitHub Desktop, hits a conflict on `secret.txt`.
3. Victim opens "Resolve Conflicts with Copilot" and views the diff/resolution options for `secret.txt`.
4. `getResolutionDiff()` executes `readFile(Path.join(repository.path, 'secret.txt'), 'utf8')`, which — following the symlink — reads `/home/victim/.ssh/id_rsa` instead of a file confined to the repository, and the private key content is rendered as "on-disk" diff content in the resolution dialog (and potentially forwarded as part of the Copilot resolution content/diff payload).

Note: I could not fully trace whether the resulting diff content from `getResolutionDiff` is subsequently transmitted to any external/Copilot API endpoint (which would upgrade this from local disclosure-in-UI to network exfiltration); this would require reading `copilot-conflicts-changes.tsx` and the Copilot request-building code end-to-end, which was not fully covered in the available index snippets.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L390-401)
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

**File:** app/src/lib/git/diff.ts (L460-463)
```typescript
  const baseContent = await readFile(
    Path.join(repository.path, filePath),
    'utf8'
  )
```
