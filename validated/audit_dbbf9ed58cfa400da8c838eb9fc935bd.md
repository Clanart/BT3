### Title
Path-containment check in `resolveWithin` uses naive prefix matching, allowing a malicious `x-github-client://openRepo` deep link to escape the cloned repository directory - (File: `app/src/lib/path.ts`)

### Summary
The reported Alchemix bug is fundamentally about a boundary/containment check that is computed correctly in the common case but silently breaks in an edge case, letting an attacker-influenced value cross a trust boundary it was supposed to be confined to (`epochRevenues` accounting escaping its per-epoch bucket). The Desktop analog is `_resolveWithin` in [1](#0-0) , which is the single guard used to keep a deep-link-supplied `filepath` confined to a repository's working directory. Its containment test uses plain string-prefix comparison instead of a boundary-aware comparison, so a sibling directory whose name merely starts with the same characters as the repository root passes the check and lets the resolved path escape the repository.

### Finding Description
`resolveWithin(rootPath, ...pathSegments)` is meant to guarantee "the resolved path is guaranteed to reside at, or underneath, `rootPath`" [2](#0-1) . The actual check is:

```
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 

`String.prototype.startsWith` has no notion of path-segment boundaries. If the repository lives at `/Users/foo/Documents/GitHub/desktop` and the same parent directory also contains a differently-owned/cloned folder `/Users/foo/Documents/GitHub/desktop-secrets` (a completely ordinary occurrence — forks, `-private`, `-old`, `-backup` sibling clones are common), then:

- `realRoot` = `/Users/foo/Documents/GitHub/desktop`
- `realResolved` = `/Users/foo/Documents/GitHub/desktop-secrets/config.json`
- `realResolved.startsWith(realRoot)` → **true**, because the string `"desktop-secrets"` starts with `"desktop"`.

The containment guard therefore approves a path that is *not* underneath the repository at all — exactly the same class of failure as the report's root cause: a boundary/state check that assumes it is being computed on data confined to the intended scope, but fails to validate the actual boundary and lets an out-of-scope value slip through and be treated as if it belonged to the trusted set.

This function is the sole defense used when handling the `filepath` parameter of an attacker-controlled `x-github-client://openRepo/...?filepath=...` deep link:

```
if (filepath !== null) {
  if (isAbsolute(filepath)) {
    log.error(`Refusing to open absolute path: ${filepath}`)
    return
  }
  const resolved = await resolveWithin(repository.path, filepath)
  if (resolved !== null) {
    shell.showItemInFolder(resolved)
  } else {
    log.error(`Prevented attempt to open path outside of the repository root: ${filepath}`)
  }
}
``` [4](#0-3) 

`filepath` and `url` come directly from `parseAppURL`, which only validates that `filepath` is a plain query-string value with no traversal-specific sanitization of its own beyond what `isAbsolute`/`resolveWithin` provide [5](#0-4) . The `isAbsolute` check only blocks absolute paths; a relative path like `../desktop-secrets/config.json` sails through it and then defeats `resolveWithin`'s prefix check as shown above.

`resolveWithin` (and its `posix`/`win32` variants) is also relied on elsewhere as a security boundary, e.g. in `app-store.ts` and `copilot-conflict-context.ts` (per `grep_search`, 2 call sites in each), so the same flawed primitive is reused as the trust boundary in multiple places, though this report focuses on the deep-link path since it is the clearest unprivileged, remotely-triggerable entry point.

### Impact Explanation
Falls squarely under "a link or deep link the user clicks" leading to "file write or read outside the repo." An attacker who gets a victim to click a crafted `x-github-client://openRepo/<attacker-repo-url>?filepath=../<sibling-name-prefix>/<target>` link can cause Desktop to call `shell.showItemInFolder()` on a path outside the intended repository, as long as a sibling directory exists whose name is a prefix-extension of the repo's folder name (a very common naming pattern for forks, `-old`, `-backup`, `-private`, versioned clones, etc.) — the attacker does not need to control the sibling directory's contents, just needs the victim's existing folder layout to have a name collision of this shape, or can increase the odds by first tricking the user into cloning the "legitimate-looking" repository into a directory whose name is a prefix of another sensitive folder. `shell.showItemInFolder` reveals the target file's existence/location in the OS file manager, which is itself an information-disclosure primitive and, depending on downstream handling of `filepath` (drag/drop, "reveal in Finder/Explorer" workflows), can be leveraged to point the user at unintended files.

### Likelihood Explanation
The deep-link handler is reachable by any external actor able to get a URL opened by the victim (email, webpage, chat) — no prior local access, credentials, or malware needed, matching the valid-impact criteria. The precondition (a sibling folder whose name shares a prefix with the cloned repository name) is a matter of ordinary directory layout rather than attacker-controlled state, so likelihood is opportunistic/environment-dependent rather than guaranteed on every victim machine, but the class of directory names it affects (`repo` vs `repo-old`/`repo-secrets`/`repo2`) is common enough to be realistic.

### Recommendation
Fix the containment check in `_resolveWithin` (`app/src/lib/path.ts` lines 66-71) to be boundary-aware, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Apply the same fix to the `win32`/`posix` variants using their respective separators, and add regression tests (in `app/test/unit/path-test.ts`) covering the sibling-directory-with-shared-prefix case.

### Proof of Concept
1. On disk: `/Users/victim/Documents/GitHub/myrepo` (a Desktop-managed repository) and `/Users/victim/Documents/GitHub/myrepo-secrets/config.json` (any other folder that happens to share the prefix).
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/attacker/myrepo?filepath=..%2Fmyrepo-secrets%2Fconfig.json`
3. `parseAppURL` decodes this into `{ name: 'open-repository-from-url', url: 'https://github.com/attacker/myrepo', filepath: '../myrepo-secrets/config.json' }` per [6](#0-5) .
4. `Dispatcher.openRepositoryFromUrl` opens/clones the repo, then calls `resolveWithin(repository.path, '../myrepo-secrets/config.json')` [4](#0-3) .
5. Inside `_resolveWithin`, `resolved` = `/Users/victim/Documents/GitHub/myrepo-secrets/config.json`; `realResolved.startsWith(realRoot)` evaluates true because `"myrepo-secrets".startsWith("myrepo")`, so the function returns the resolved (out-of-repo) path instead of `null`.
6. `shell.showItemInFolder(resolved)` reveals `config.json` from the unrelated `myrepo-secrets` folder, confirming the containment boundary was bypassed.

I was not able to execute this against a live Desktop build (no runtime/browser access), so the PoC is a static code-path trace based on the source shown above; a Devin session with the actual app running would be needed to confirm the end-to-end UI behavior.

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

**File:** app/src/lib/path.ts (L36-72)
```typescript
async function _resolveWithin(
  rootPath: string,
  pathSegments: string[],
  options: {
    join: (...pathSegments: string[]) => string
    normalize: (p: string) => string
    resolve: (...pathSegments: string[]) => string
  } = Path
) {
  // An empty root path would let all relative
  // paths through.
  if (rootPath.length === 0) {
    return null
  }

  const { join, normalize, resolve } = options

  const normalizedRoot = normalize(rootPath)
  const normalizedRelative = normalize(join(...pathSegments))

  // Null bytes has no place in paths.
  if (
    normalizedRoot.indexOf('\0') !== -1 ||
    normalizedRelative.indexOf('\0') !== -1
  ) {
    return null
  }

  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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
