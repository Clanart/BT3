Confirmed: `resolveWithin` (and thus `resolveWithinPosix`/`resolveWithinWin32`) in `app/src/lib/path.ts` contains a boundary-check bug analogous to the reported "loose comparison lets excess slip past the intended boundary" bug class.

### Title
Sibling-directory path-containment bypass via missing separator check in `resolveWithin()` - (File: `app/src/lib/path.ts`)

### Summary
The `MysteryBox` bug's root cause is a loose comparison (`>=` instead of `==`) that lets an "excess" amount slip past the intended boundary and be misdirected. The Desktop analog is the containment check in `_resolveWithin()`, which uses a bare `String.prototype.startsWith()` to decide whether a resolved path is "inside" a root directory: `realResolved.startsWith(realRoot)` [1](#0-0) . This check has no path-separator boundary, so any sibling path whose name has the root's basename as a string prefix (e.g. root `/a/b`, target `/a/bx`) is incorrectly treated as "underneath" the root.

### Finding Description
`resolveWithin()`/`resolveWithinPosix()`/`resolveWithinWin32()` are the app's central "keep this path inside that repo" primitive [2](#0-1) . It normalizes and resolves the target, then validates containment purely with a string prefix check:

```
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [1](#0-0) 

Because `startsWith` does not require a trailing path separator after `realRoot`, a resolved path like `/Users/victim/Documents/GitHub/repo-secret` passes the check against root `/Users/victim/Documents/GitHub/repo`, since `"...repo-secret".startsWith("...repo")` is `true` even though `repo-secret` is a completely different, sibling directory. The existing regression tests only exercise `..`-escape and symlink-escape cases and never test the sibling/prefix-collision scenario [3](#0-2) , so this gap is unguarded.

This primitive is consumed directly by attacker-influenced input from a deep link: `dispatcher.ts`'s `openRepositoryFromUrl()` takes the `filepath` query parameter from an `x-github-client://openRepo/...` URL (parsed by `parseAppURL()` [4](#0-3) ) and calls `resolveWithin(repository.path, filepath)`, then acts on the result if non-null:

```
const resolved = await resolveWithin(repository.path, filepath)
if (resolved !== null) {
  shell.showItemInFolder(resolved)
} else {
  log.error(`Prevented attempt to open path outside of the repository root: ${filepath}`)
}
``` [5](#0-4) 

`resolveWithin` is also used to gate file access from `app-store.ts` and `copilot-conflict-context.ts` , so any caller relying on it for a security boundary inherits the same flaw.

### Impact Explanation
An attacker who controls (or gets a victim to click) a `github-mac://openRepo/...` / `x-github-client://openRepo/...` deep link, or crafts a `filepath` query parameter combined with a `..`-relative segment, can cause the resolved path to land in a sibling directory that merely shares the repository directory's name as a prefix (a plausible scenario given how GitHub Desktop derives clone folder names directly from repo names, e.g. `repo` vs. `repo-notes`, `repo-old`, `repo.bak`, `repository`). The containment check will wrongly accept this as "inside" the repository and reveal/act on a file the user never intended to expose, without any of the guards (`isAbsolute` check, `resolveWithin`) actually stopping it. Today the only sink using this exact path is `shell.showItemInFolder()`, which discloses the existence/location of a file outside the repo, but the same broken primitive backs other file-path validations in `app-store.ts` and `copilot-conflict-context.ts`, so the blast radius depends on what those callers do with the returned path (this could not be fully verified with static/read-only tools; a Devin session with full repo access should confirm which of those other call sites' consequences may be more severe, e.g. writing or reading file contents to/from the falsely-accepted path).

### Likelihood Explanation
The attacker fully controls the crafted deep link's `filepath`/`url` parameters, and the only requirement for exploitation is that a sibling path exists whose name is an extension of the repo directory's basename — a common naming pattern for backups/related folders. No local access, privileges, or prior compromise is needed; only a single click on a link is required, consistent with GitHub Desktop's registered custom protocol handler wiring in `main.ts` (`app.on('open-url', ...)` → `handleAppURL` → `parseAppURL`) [6](#0-5) .

### Recommendation
Fix `_resolveWithin()` in `app/src/lib/path.ts` to require an exact match or a path-separator boundary after the root, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
```
(using the platform-specific separator for the `options` variant passed in), and add a regression test asserting that a sibling directory sharing the root's basename as a prefix is rejected.

### Proof of Concept
1. Victim has a repository cloned at `/Users/victim/Documents/GitHub/repo` and also happens to have an unrelated folder `/Users/victim/Documents/GitHub/repo-secret` (or any sibling whose name starts with `repo`).
2. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/owner/repo?filepath=..%2Frepo-secret%2Fsensitive-file`.
3. Victim clicks the link; `handleAppURL` → `parseAppURL` extracts `filepath = "../repo-secret/sensitive-file"` [7](#0-6) .
4. `openRepositoryFromUrl` calls `resolveWithin(repository.path, filepath)`; `_resolveWithin` resolves to `/Users/victim/Documents/GitHub/repo-secret/sensitive-file`, and `realResolved.startsWith(realRoot)` (root = `.../repo`) evaluates `true` due to the missing separator check, so the guard is bypassed [1](#0-0) .
5. `shell.showItemInFolder(resolved)` opens/reveals a file that lives entirely outside the cloned repository, silently defeating the "Prevented attempt to open path outside of the repository root" protection the code believes it enforces [8](#0-7) .

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

**File:** app/src/lib/path.ts (L68-71)
```typescript
  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/test/unit/path-test.ts (L44-63)
```typescript
  describe('resolveWithin', () => {
    const root = process.cwd()

    it('fails for paths outside of the root', async () => {
      assert((await resolveWithin(root, join('..'))) === null)
      assert((await resolveWithin(root, join('..', '..'))) === null)
    })

    it('succeeds for paths that traverse out, and then back into, the root', async () => {
      assert.equal(await resolveWithin(root, join('..', basename(root))), root)
    })

    it('fails for paths containing null bytes', async () => {
      assert((await resolveWithin(root, 'foo\0bar')) === null)
    })

    it('succeeds for absolute relative paths as long as they stay within the root', async () => {
      const parent = resolve(root, '..')
      assert.equal(await resolveWithin(parent, root), root)
    })
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

**File:** app/src/main-process/main.ts (L204-210)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
})
```
