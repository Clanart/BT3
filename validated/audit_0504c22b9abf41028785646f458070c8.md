### Title
Path-containment check in `resolveWithin` uses a bare string-prefix comparison, allowing symlink/sibling-directory boundary escape - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin` is the shared guard Desktop uses whenever it needs to prove that an attacker-influenced relative path stays inside a trusted root directory (a cloned repository, a worktree, etc.). The final containment decision is made with a raw `String.prototype.startsWith` comparison between the real (symlink-resolved) root and the real resolved target, with no path-separator boundary check. This is the same bug class as the reported lending issue: a security-relevant comparison is performed against the wrong/insufficiently-precise value, so the guard silently passes for inputs that violate the invariant it exists to enforce.

### Finding Description
`app/src/lib/path.ts:36-72`:

```ts
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [1](#0-0) 

The intent (per the doc comment) is “the resolved path is guaranteed to reside at, or underneath, the root path” [2](#0-1) . But `startsWith` treats the root as a literal character prefix, not a directory boundary. If `realRoot` is `/Users/victim/Documents/GitHub/repo` and a symlink inside the repository resolves to `/Users/victim/Documents/GitHub/repo-secrets/token.txt`, the check passes because the string `"…/repo-secrets/token.txt"` starts with `"…/repo"` — even though `repo-secrets` is a completely different, sibling directory that has nothing to do with the trusted root.

This guard is reached from attacker-influenced input in at least two places:
- `Dispatcher.openRepositoryFromUrl`, which handles the `x-github-client://openRepo` deep link. The `filepath` query parameter (fully attacker controlled via a crafted link) is joined against `repository.path` and validated with exactly this function before being handed to `shell.showItemInFolder`: [3](#0-2) 
- The Copilot conflict-context code and `app-store.ts` also call the same `resolveWithin`/`resolveWithinPosix` helpers to keep file reads bounded to the repository, per the grep hits in `app/src/lib/copilot-conflict-context.ts` and `app/src/lib/stores/app-store.ts`.

Because a cloned/fetched repository is attacker-controlled content, the attacker can place a symlink inside the working tree (Git preserves symlinks) whose target, once resolved with `realpath`, lands in a sibling directory that merely shares the root path as a string prefix (e.g., another cloned repo, a backup folder, or any directory the victim happens to have alongside the trusted root). The `startsWith` check does not require the next character after the shared prefix to be a path separator, so this sibling path is incorrectly treated as “inside” the root.

### Impact Explanation
Where this guard is used to gate `shell.showItemInFolder` (deep link handling) or file reads for the Copilot conflict-resolution feature, a bypass lets a malicious repository content (via a crafted deep link plus a symlink placed in the repo) cause Desktop to open/read a file outside the intended repository boundary — exactly the “file write or read outside the repo” impact category called out as valid. The blast radius is bounded by what sibling paths exist and are reachable via a string-prefix collision, but no additional privilege or local access is required beyond the user cloning/opening the attacker’s repository and clicking a link, which are both normal, expected Desktop workflows.

### Likelihood Explanation
Likelihood is limited by the need for a “lucky” or contrived directory naming collision (e.g., root `.../repo` and target `.../repo-something`), which is not guaranteed to exist on a given victim’s machine, and by the deep-link path requiring the user to click an `x-github-client://` link. It is not a universal, always-triggerable bug like the original lending report, but the underlying flaw — a security check built on a plain string-prefix comparison instead of a boundary-aware comparison (`path.relative` or `startsWith(root + sep)`) — is present in one canonical function shared by multiple attacker-reachable call sites, so any future or existing directory-naming pattern that satisfies the prefix collision defeats the guard silently, with no error or warning.

### Recommendation
Fix `_resolveWithin` to compare directory boundaries, not raw string prefixes, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(with the platform-appropriate separator for `options` being `Path.win32.sep`/`Path.posix.sep`). Apply the same fix to `resolveWithinPosix`/`resolveWithinWin32` since they share `_resolveWithin`. Add regression tests covering sibling-directory prefix collisions (e.g., root `/a/b` vs. resolved `/a/bc`).

### Proof of Concept
1. Attacker publishes a repository containing a symlink `escape` that points to `../<victim-sibling-dir>/secret.txt` (Git supports committing symlinks).
2. Attacker crafts a deep link: `x-github-client://openRepo/https://github.com/attacker/repo?filepath=escape`.
3. Victim clicks the link; Desktop clones/opens the repo at, say, `/Users/victim/Documents/GitHub/repo`, then calls `resolveWithin(repository.path, 'escape')` in `openRepositoryFromUrl` (`app/src/ui/dispatcher/dispatcher.ts:1963`).
4. `realpath` resolves the symlink to an absolute path such as `/Users/victim/Documents/GitHub/repo-backup/secret.txt`.
5. `realResolved.startsWith(realRoot)` evaluates to `true` because `"/Users/victim/Documents/GitHub/repo-backup/secret.txt".startsWith("/Users/victim/Documents/GitHub/repo")` is `true`, even though `repo-backup` is not inside `repo`.
6. `resolveWithin` returns a non-null path and `shell.showItemInFolder(resolved)` is invoked on a location outside the intended repository, bypassing the “Prevented attempt to open path outside of the repository root” guard entirely (`app/src/ui/dispatcher/dispatcher.ts:1963-1971`).

### Citations

**File:** app/src/lib/path.ts (L13-24)
```typescript
/**
 * Resolve one or more path sequences into an absolute path underneath
 * or at the given root path.
 *
 * The path segments are expected to be relative paths although
 * providing an absolute path is also supported. In the case of an
 * absolute path segment this method will essentially only verify
 * that the absolute path is equal to or deeper in the directory
 * tree than the root path.
 *
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
```

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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
