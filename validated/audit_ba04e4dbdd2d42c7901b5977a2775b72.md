## Title
`resolveWithin()` sandbox-escape via unanchored `startsWith(realRoot)` prefix check allows attacker-controlled deep-link `filepath` to reach sibling directories - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin()` is Desktop's generic "confine this path to root" guard, used to validate a user/attacker-supplied relative path before it is turned into a real filesystem path. Its final containment check is:

```ts
return realResolved.startsWith(realRoot) ? resolved : null
``` [1](#0-0) 

This is the exact bug class described in the CKB report: a boundary/containment check implemented as a raw byte/string prefix comparison without ensuring the match ends on a full path-segment boundary. Just as `01 4c bc...86 6f`-style truncation let a 21-byte value satisfy a bound meant for a 22-byte prefix, `"/Users/victim/Documents/GitHub/repo-evil".startsWith("/Users/victim/Documents/GitHub/repo")` is `true` even though `repo-evil` is a completely different, sibling directory from `repo`.

### Finding Description
`resolveWithin(rootPath, ...pathSegments)` is documented to guarantee the resolved path "resides at, or underneath" `rootPath` [2](#0-1) . It normalizes the input, joins/resolves it against the root, resolves symlinks with `realpath`, and then performs the containment check purely via string prefix comparison, with no verification that `realRoot` is followed by a path separator (or that the match is an exact equal length) in `realResolved`:

```ts
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)

return realResolved.startsWith(realRoot) ? resolved : null
``` [3](#0-2) 

Because `join`/`resolve` are used first, an attacker cannot use ordinary `..` traversal to land in a sibling directory that isn't reachable by relative navigation from the root's *parent* — but they don't need to: if the repository lives at `/Users/victim/Documents/GitHub/repo` and a sibling directory `/Users/victim/Documents/GitHub/repo-backup` (or `repo.bak`, `repository`, `repo2`, etc.) exists, then a relative path such as `../repo-backup/secret.txt` resolves to `/Users/victim/Documents/GitHub/repo-backup/secret.txt`, whose `realpath` legitimately **starts with** `realRoot` (`/Users/victim/Documents/GitHub/repo`) as a raw string, even though it is not underneath it at all. The check passes and `resolved` is returned as "safe."

This is consumed directly with attacker-influenced input in the deep-link handler:

```ts
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

`filepath` originates from an `x-github-client://openLocalRepo/...` deep link (`IOpenRepositoryFromURLAction`), i.e. from a link an attacker can get the victim to click — exactly the "link/deep link the user clicks" primitive called out as in-scope. `resolveWithin` is also relied upon in `app-store.ts` and `copilot-conflict-context.ts` [5](#0-4)  as the general sandbox primitive for confining paths, so any additional caller that trusts its guarantee inherits the same flaw.

The existing regression tests only cover `..`-only escapes and symlink escapes, not the sibling-prefix case, and only assert that traversal *outside and back in* is allowed and that pure `..` escapes are blocked — no test exercises a same-named-prefix sibling directory, so this specific defect is unguarded by the test suite: [6](#0-5) 

### Impact Explanation
`resolveWithin` is Desktop's core "don't let this path escape the repo" primitive. A bypass lets an attacker-crafted deep link cause Desktop to treat a path outside of the intended repository as validated/contained, and hand it to `shell.showItemInFolder()` (which reveals — and on some platforms can be chained with further OS interactions on — the target file/folder) even though it is a sibling directory the user never intended to expose via that link. Because `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` are the shared containment primitive used elsewhere in the codebase (e.g. `app-store.ts`, `copilot-conflict-context.ts`), any future or existing caller that assumes "startsWith real root ⇒ safely contained" is subject to the same read/reveal-outside-repo class of issue, matching the "file write or read outside the repo" impact bucket.

### Likelihood Explanation
Exploitation requires: (1) the victim clicks an attacker-supplied deep link with a `filepath` parameter, and (2) a directory happens to (or is caused to) exist alongside the target repository whose name has the repository's directory name as a literal prefix (e.g., `repo`, `repo-backup`, `repo.bak`, `repository`). Condition (2) is not always attacker-controlled, which limits reliability somewhat compared to a fully generic escape — but many real-world working folders do contain such naturally-named siblings (backup copies, forks, renamed clones), and the check offers zero defense-in-depth once such a sibling exists. No local/physical access, admin rights, or pre-existing malware is required — only a single click on a link, matching the "unprivileged... a link or deep link the user clicks" criterion.

### Recommendation
Fix the containment check in `_resolveWithin()` to require an exact match or a match terminated by the platform path separator, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
using the separator appropriate to the `options` passed in (`Path.sep`, `Path.win32.sep`, or `Path.posix.sep`), and add a regression test for a sibling directory that shares a name prefix with the root (e.g. `repo` vs `repo-evil`).

### Proof of Concept
1. Victim has a repository open at `/Users/victim/Documents/GitHub/repo` and, elsewhere in the same parent folder, a directory `/Users/victim/Documents/GitHub/repo-backup/secret.txt` (a common pattern — backups, old clones, etc.).
2. Attacker sends the victim a deep link: `x-github-client://openLocalRepo/repo?filepath=../repo-backup/secret.txt`.
3. `openRepositoryFromUrl` calls `resolveWithin(repository.path, '../repo-backup/secret.txt')` [7](#0-6) .
4. Inside `_resolveWithin`, `resolved` becomes `/Users/victim/Documents/GitHub/repo-backup/secret.txt`, `realRoot` is `/Users/victim/Documents/GitHub/repo`, and `realResolved.startsWith(realRoot)` evaluates `true` because of the shared literal prefix `.../GitHub/repo` [8](#0-7) .
5. The check incorrectly passes, and `shell.showItemInFolder(resolved)` reveals `secret.txt` from the sibling directory — a location entirely outside the intended repository — to the attacker's deep link target, with no traversal warning logged.

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

**File:** app/src/lib/path.ts (L66-72)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L1-1)
```typescript
import * as Path from 'path'
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
