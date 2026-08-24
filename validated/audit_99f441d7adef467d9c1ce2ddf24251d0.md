### Title
Path-containment check in `resolveWithin` uses a bare string prefix match, letting a sibling directory bypass the "inside root" guard - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin` (and its `Win32`/`Posix` variants) is the shared utility Desktop uses to guarantee that a path derived from external/untrusted input stays inside a trusted root directory (e.g. the user's repository folder) before touching the filesystem or shell. [1](#0-0)  Its actual boundary check is a raw `String.prototype.startsWith` comparison between the resolved real path and the real root path, with no verification that the match ends on a path-separator boundary:

```ts
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

This is the same bug class as the Anchor single-byte discriminator issue: a *short/insufficient* identity comparison ("is `realRoot` a byte-prefix of `realResolved`?") is used where an *exact boundary* comparison is required, so an unrelated sibling object that happens to share the trusted value as a literal prefix (`/…/repo-secrets` vs `/…/repo`) is accepted as if it were "inside" the trusted object (`repo`).

### Finding Description
`_resolveWithin` normalizes and joins the caller-supplied path segments with the root, resolves the real (symlink-resolved) path, and then decides containment purely by string prefix:

```ts
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 

Because `startsWith` has no notion of path segment boundaries, any directory whose name textually begins with the root directory's name — a sibling such as `repo-secrets`, `repo2`, `repository-backup` — will satisfy `realResolved.startsWith(realRoot)` even though it is not a descendant of `realRoot` at all. This is the direct analog of the discriminator report: a partial/prefix match is trusted as a full identity match, and the entity outside the intended boundary (a differently-named directory) is treated as belonging to it.

The exploitable path is reachable via GitHub Desktop's `x-github-client://openRepo/...` deep link, which an attacker fully controls (a link the victim clicks). `parseAppURL` extracts an attacker-supplied `filepath` query parameter with no path-traversal sanitization beyond checking it isn't already absolute: [4](#0-3) 

`Dispatcher.openRepositoryFromUrl` only rejects `isAbsolute(filepath)`, then relies entirely on `resolveWithin` for containment before calling `shell.showItemInFolder`:
```ts
if (isAbsolute(filepath)) {
  log.error(`Refusing to open absolute path: ${filepath}`)
  return
}
const resolved = await resolveWithin(repository.path, filepath)
if (resolved !== null) {
  shell.showItemInFolder(resolved)
} else { ... }
``` [5](#0-4) 

A relative `filepath` such as `../repo-secrets/notes.txt` survives the `isAbsolute` check. After `join`/`normalize` inside `resolveWithin`, the resulting real path becomes `/Users/victim/Documents/GitHub/repo-secrets/notes.txt` — a sibling of the repository directory, not inside it — yet the string `.../repo-secrets/notes.txt` still literally `startsWith` `.../repo` (the repository root), so the existing guard silently accepts a path that is outside the intended repository tree.

### Impact Explanation
This breaks the exact security invariant `resolveWithin` exists to enforce: "the returned path resides at, or underneath, the given root." An attacker-crafted deep link (no local access, no credentials, just a link the victim clicks) can cause Desktop to resolve and act on a path in a sibling directory outside the target repository. In the concrete `openRepositoryFromUrl` sink this yields disclosure/reveal of a file outside the repository via `shell.showItemInFolder`; because `resolveWithin` is the generic containment primitive relied on elsewhere in the codebase whenever untrusted relative segments must be confined to a directory, any other caller that also uses it as the sole containment guard before a file read/write inherits the same boundary bypass. This is a silent-corruption/confused-deputy class issue matching the report's "wrong object accepted because of an insufficient identity check" pattern.

### Likelihood Explanation
High: the attacker only needs to get the victim to click a crafted `x-github-client://openRepo/...?filepath=../<siblingdir-prefix-match>/...` link; no local access, no elevated privileges, and no unnatural manual steps beyond the normal "Open in Desktop" flow are required. The precondition (a sibling directory whose name starts with the repository directory's name) is common in practice (e.g., cloning `repo` and `repo-old`, `repo2`, `repo.bak` side-by-side in the same parent folder, which GitHub Desktop's default clone-path behavior encourages).

### Recommendation
Fix the containment check in `_resolveWithin` in `app/src/lib/path.ts` to require an exact path-segment boundary, not a bare string prefix, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + sep)
  ? resolved
  : null
```
using the platform-appropriate separator (`options` already carries `join`/`normalize`/`resolve`, so a matching `sep` should be threaded through as well), mirroring how the discriminator fix required checking the full 8 bytes instead of a variable-length prefix.

### Proof of Concept
1. Victim has two folders side by side: `~/Documents/GitHub/repo` (a tracked repository) and `~/Documents/GitHub/repo-secrets` (any other folder, e.g. containing sensitive notes).
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/owner/repo?filepath=..%2Frepo-secrets%2Fnotes.txt`
3. Victim clicks the link; Desktop parses it via `parseAppURL` [4](#0-3) , dispatches to `openRepositoryFromUrl`, `isAbsolute('../repo-secrets/notes.txt')` is `false` so the guard passes.
4. `resolveWithin(repository.path, '../repo-secrets/notes.txt')` normalizes/joins to `~/Documents/GitHub/repo-secrets/notes.txt`, and the boundary check `realResolved.startsWith(realRoot)` returns `true` because `"…/repo-secrets/notes.txt"` starts with `"…/repo"`.
5. `shell.showItemInFolder(resolved)` reveals/operates on a file outside the intended repository directory, even though the code believed it had confined the operation to the repo root.

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

**File:** app/src/lib/parse-app-url.ts (L98-124)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
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
