### Title
Prefix-only path-containment check in `resolveWithin` allows sibling-directory escape via crafted deep-link `filepath` - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin()` is the app-wide guard used to guarantee that a user/attacker-supplied relative path cannot resolve outside of a trusted root directory (e.g. the repository working directory). It is used, among other places, to sanitize the `filepath` query parameter of the `x-github-client://openRepo/...` deep link before the app reveals that file to the user with `shell.showItemInFolder()`. The containment check, however, is implemented as a bare string prefix comparison (`realResolved.startsWith(realRoot)`) with no path-separator boundary check, so any sibling directory whose name happens to begin with the repository directory’s name will incorrectly be treated as “inside” the repository. [1](#0-0)  This mirrors the report’s broken-invariant pattern: a guard exists in the code and looks correct, but a subtle implementation flaw means the invariant it is supposed to enforce (“resolved path stays under root”) does not actually hold in every case.

### Finding Description
`resolveWithin(rootPath, ...pathSegments)` resolves the segments against `rootPath` and is documented to guarantee the result "resides at, or underneath" the root: [2](#0-1) . The actual containment test is:

```
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 

`String.prototype.startsWith` performs a raw character comparison, not a directory-boundary comparison. If `realRoot` is `/Users/victim/Documents/GitHub/project` and an attacker-controlled relative segment resolves (after realpath) to `/Users/victim/Documents/GitHub/project-secrets/config.json`, the check still returns `true` because the string `"project-secrets/config.json"` begins with `"project"`. The function therefore returns a path that is **not** actually inside the intended root — the exact same class of bug as the report’s broken re-entrancy guard: the safety mechanism exists syntactically but the logic contains a gap that defeats it under a specific, attacker-reachable condition.

This guard is consumed directly by the deep-link handler for the `open-repository-from-url` action:

```ts
if (filepath !== null) {
  if (isAbsolute(filepath)) {
    log.error(`Refusing to open absolute path: ${filepath}`)
    return
  }
  const resolved = await resolveWithin(repository.path, filepath)
  if (resolved !== null) {
    shell.showItemInFolder(resolved)
  } else { ... }
}
``` [4](#0-3) 

`filepath` comes straight from the `x-github-client://openRepo/...?filepath=...` protocol URL parsed by `parseAppURL`, which performs no traversal filtering on `filepath` (only `pr` and `branch` are validated) [5](#0-4) . The only defenses before `shell.showItemInFolder` are the `isAbsolute()` check (trivially bypassed by using a relative `..`-based path) and `resolveWithin`, which is the vulnerable function described above.

### Impact Explanation
An attacker who gets a victim to click a crafted `x-github-client://openRepo/...` link (an ordinary, unprivileged web link/HTML anchor — no local access, admin rights, or prior compromise required) can supply a `filepath` value such as `../project-secrets/.env`. If the victim has any sibling directory next to the target repository whose name starts with the repository’s directory name (a common real-world layout, e.g. `project`, `project-old`, `project-backup`, `project2`, `project.bak`), the traversal check silently accepts the escape and Desktop reveals/opens a file outside the repository in the OS file manager. The same `resolveWithin` primitive is reused elsewhere in the codebase as the general path-containment guard (e.g. for conflict-file resolution in `buildConflictContext`), so the flaw is not confined to a single call site — anywhere this function is relied upon to keep file access inside a trusted directory, the prefix-only check can be defeated the same way, resulting in disclosure of files outside the intended repository boundary.

### Likelihood Explanation
Medium. Exploitation only requires: (1) the victim to click an attacker-supplied deep link (a normal, low-friction phishing/web vector, not requiring local access or credentials), and (2) the existence of a sibling directory sharing the target repository’s name as a literal prefix — a layout that is common for developers who keep multiple clones/variants of a project in the same parent folder. No authentication, elevated privileges, or pre-existing malware is required, and the URL-scheme handler is registered and reachable from any web page or message the victim opens with the “x-github-client” protocol.

### Recommendation
Fix `resolveWithin` in `app/src/lib/path.ts` to enforce an actual directory-boundary check rather than a raw string prefix comparison, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Apply the same fix to `resolveWithinPosix`/`resolveWithinWin32` (which share the same underlying `_resolveWithin` implementation), and add regression tests for sibling-directory-prefix inputs (e.g. root `.../project`, target `.../project-secrets/file`) to prevent recurrence.

### Proof of Concept
1. Victim has GitHub Desktop with a cloned repository at `~/Documents/GitHub/project` and, elsewhere in the same parent folder, a directory `~/Documents/GitHub/project-secrets` containing a sensitive file `config.json`.
2. Attacker sends the victim a link (e.g. embedded in an email or web page):
   `x-github-client://openRepo/https://github.com/owner/project?filepath=../project-secrets/config.json`
3. Victim clicks the link; the OS invokes Desktop's registered protocol handler, which calls `handleAppURL` → `parseAppURL` → `dispatchURLAction` → `openRepositoryFromUrl`. [6](#0-5) 
4. `filepath` (`../project-secrets/config.json`) is relative, so it passes `isAbsolute()`; `resolveWithin('~/Documents/GitHub/project', '../project-secrets/config.json')` resolves to `~/Documents/GitHub/project-secrets/config.json`, whose real path string starts with the real path of `~/Documents/GitHub/project`, so the guard incorrectly returns a non-null path.
5. `shell.showItemInFolder(resolved)` reveals the out-of-repository sensitive file to the user, confirming the traversal succeeded despite the guard. [7](#0-6)

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

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
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
