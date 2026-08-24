### Title
Path-containment check in `resolveWithin` uses unanchored prefix match, allowing deep-link `filepath` parameter to escape the intended repository directory - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin` is Desktop's generic guard for "is this resolved path inside that root directory" and is relied on to sandbox filesystem access derived from untrusted input. [1](#0-0)  Its final containment test is a raw string-prefix comparison (`realResolved.startsWith(realRoot)`) without checking for a path-separator boundary after the prefix, so a sibling directory whose name simply begins with the root directory's name is incorrectly treated as "inside" the root. [2](#0-1)  This mirrors the report's broken invariant: a check meant to bind an operation to a specific, precisely-identified container (leaf index / directory boundary) instead accepts any input that merely shares a prefix with the expected value, because the implementation never asserts the missing boundary condition.

### Finding Description
`_resolveWithin` computes `resolved = resolve(normalizedRoot, normalizedRelative)`, then real-path-resolves both `root` and `resolved`, and finally validates containment purely via:
```
return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 

Because `realRoot` has no guaranteed trailing separator, `startsWith` succeeds for any `realResolved` whose path text begins with `realRoot`, even if it is actually a *different, sibling* directory (e.g. root `/Users/victim/Documents/GitHub/react` vs. resolved `/Users/victim/Documents/GitHub/react-native/secret.env`). The `..` segments in `filepath` are legitimately resolved by `path.resolve`/`path.normalize` (this part is correct and blocks simple `../../` escapes to unrelated top-level paths), but the final "is it still under root" gate does not verify that the boundary after the shared prefix is a path separator, so it silently accepts any target directory that lexically starts with the root's name.

This is directly reachable from attacker-controlled input via GitHub Desktop's custom URL protocol handler. `handleAppURL` in `app/src/main-process/main.ts` forwards any `x-github-client://…` (or `github-mac://…`) URL from `open-url`/CLI args straight into `parseAppURL`. [4](#0-3)  `parseAppURL` builds an `IOpenRepositoryFromURLAction` and only validates `pr`/`branch` fields — the `filepath` query parameter is passed through completely unsanitized (no `testForInvalidChars`, no traversal check). [5](#0-4) 

`Dispatcher.openRepositoryFromUrl` then resolves an already-known/cloned repository (matched purely by remote URL, requiring no additional user confirmation) and calls the vulnerable guard directly with the attacker-supplied `filepath`:
```ts
if (filepath !== null) {
  if (isAbsolute(filepath)) { ... return }
  const resolved = await resolveWithin(repository.path, filepath)
  if (resolved !== null) {
    shell.showItemInFolder(resolved)
  } else { ... }
}
``` [6](#0-5) 

Only an absolute-path check guards this call; relative traversal to a sibling directory is exactly what `resolveWithin`'s flawed prefix check fails to reject.

### Impact Explanation
An attacker who gets a victim to click a single crafted `x-github-client://openrepo/<known-repo-url>?filepath=..%2F<sibling-dir-prefix-match>%2F<target-file>` link (no local access, no prior malware, no leaked credentials — just a link click, the exact attacker profile this task scopes as valid) can cause Desktop to resolve and reveal a path outside the intended repository whenever the victim has another repository/folder cloned as a sibling of the targeted one whose name begins with the targeted repo's directory name (a very common real-world layout, e.g. `react` next to `react-native`, `org` next to `org-internal`, or attacker-chosen upstream repo names designed to collide with common sibling naming conventions). The directly demonstrated primitive is disclosure of a file's existence/location outside the repo root via `shell.showItemInFolder`. Because `resolveWithin` is the shared sandboxing primitive (also used by `copilot-conflict-context.ts` for reading file contents that get sent into AI conflict-resolution context, and by `app-store.ts`), the same broken invariant is reachable by any other caller that trusts it to keep filesystem operations confined to a repository, which is a strictly stronger read/write-outside-repo risk than the single PoC below demonstrates.

### Likelihood Explanation
The entry point requires nothing more than the victim clicking a link (standard Desktop deep-link flow already wired into `main.ts`), and the vulnerable code path executes with zero additional confirmation as long as the referenced repository is already known to Desktop (matched by remote URL). The only extra precondition is that a sibling directory with a colliding name prefix exists, which is a common, unforced real-world condition rather than an "unnatural user step."

### Recommendation
Fix `_resolveWithin` in `app/src/lib/path.ts` to require a path-separator boundary (or exact equality) after the shared prefix, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
This is analogous to the report's recommendation to bind the check to the exact leaf index/boundary rather than accepting any value that merely satisfies a weaker structural resemblance.

### Proof of Concept
1. Victim has GitHub Desktop with two repositories cloned side by side, e.g. `~/Documents/GitHub/react` and `~/Documents/GitHub/react-native` (both already added in Desktop, matching origin URLs `https://github.com/facebook/react` and `.../react-native`).
2. Attacker sends/hosts a link:
   `x-github-client://openrepo/https://github.com/facebook/react?filepath=..%2Freact-native%2F.env`
3. Victim clicks it. `handleAppURL` → `parseAppURL` produces `{ name: 'open-repository-from-url', url: 'https://github.com/facebook/react', filepath: '../react-native/.env' }` (passes unmodified, no `testForInvalidChars` applied to `filepath`). [7](#0-6) 
4. `Dispatcher.openRepositoryFromUrl` matches the existing `react` repository and calls `resolveWithin(reactRepoPath, '../react-native/.env')`. [8](#0-7) 
5. Inside `_resolveWithin`, `resolved` correctly becomes `.../GitHub/react-native/.env`, which is genuinely outside `.../GitHub/react`; but `realResolved.startsWith(realRoot)` (`'.../GitHub/react-native/.env'.startsWith('.../GitHub/react')`) evaluates `true`, so the function returns the escaped path instead of `null`. [3](#0-2) 
6. Desktop calls `shell.showItemInFolder(resolved)`, revealing/highlighting `react-native/.env` — a file outside the repository the deep link claimed to target — confirming the sandbox boundary was bypassed via an unprivileged, user-clicked deep link.

### Citations

**File:** app/src/lib/path.ts (L13-35)
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
 *
 * @param rootPath     The path to the root path. The resolved path
 *                     is guaranteed to reside at, or underneath this
 *                     path.
 * @param pathSegments One or more paths to join with the root path
 * @param options      A subset of the Path module. Requires the join,
 *                     resolve, and normalize path functions. Defaults
 *                     to the platform specific path functions but can
 *                     be overridden by providing either Path.win32 or
 *                     Path.posix
 */
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

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
```

**File:** app/src/lib/parse-app-url.ts (L98-125)
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
  }
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
