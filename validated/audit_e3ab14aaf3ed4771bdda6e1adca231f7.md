### Title
Path-boundary check in `resolveWithin` uses unanchored `startsWith`, allowing sibling-directory escape from attacker-controlled deep-link `filepath` - (File: `app/src/lib/path.ts`)

### Summary
The reported Primitive bug is a boundary-comparison flaw (`>` instead of `>=`) in an invariant check that is supposed to guarantee "this value is definitely inside/past a boundary," which attackers exploit to slip past the intended edge. The closest structural analog in GitHub Desktop is the containment check in `_resolveWithin`, which is supposed to guarantee a resolved path stays "at or underneath" a root directory before the app is allowed to act on it. That guarantee is implemented with a raw string-prefix `startsWith` comparison instead of a boundary-aware comparison, so a sibling directory whose name happens to share the root path as a literal prefix passes the check even though it is not actually inside the root.

### Finding Description
`_resolveWithin` is the shared safety primitive that validates a path is contained within a given root before Desktop uses it: [1](#0-0) 

The final containment decision is:
```
return realResolved.startsWith(realRoot) ? resolved : null
```
This is a classic unanchored-prefix check. `String.prototype.startsWith` has no notion of a path-separator boundary, so if `realRoot` is e.g. `/Users/victim/Documents/GitHub/project` and an attacker can get the code to resolve `../project-exfil/secret.txt`, the resulting `realResolved` (`/Users/victim/Documents/GitHub/project-exfil/secret.txt`) literally starts with the string `/Users/victim/Documents/GitHub/project`, so the check returns true even though `project-exfil` is a completely different, sibling directory — not a descendant of `project`. The existing unit tests only cover traversal via `..` and a single deliberately-nested symlink case; none of them test the "root-with-a-sibling-that-shares-a-prefix" scenario, so this boundary gap has no regression coverage: [2](#0-1) 

This function is the sole containment guard used by `openRepositoryFromUrl`, which is reachable directly from an attacker-controlled deep link (`x-github-client://openRepo/...&filepath=...`). The `filepath` query parameter comes straight from the URL that the user is tricked into clicking, is only checked for being non-absolute, and is then handed to `resolveWithin(repository.path, filepath)`: [3](#0-2) 

Because `repository.path` for a freshly-cloned repo is deterministic (it is derived from the clone URL/owner/name that Desktop itself just cloned, e.g. `.../GitHub/<repo-name>`), an attacker who controls the "Open in Desktop" URL also effectively controls the exact string value of the root passed into `resolveWithin`. By choosing `filepath` such that `..` segments land the resolved path in a sibling folder whose name is `<repo-name>` + extra characters (e.g. `<repo-name>-config`, `<repo-name>2`, `<repo-name>.bak`), the attacker can make `resolveWithin` treat a directory that is a sibling of the repo — not a subdirectory of it — as "within root," because the string comparison only checks a shared prefix rather than a proper path boundary (`realRoot + path.sep`).

This mirrors the Primitive bug precisely: both are boundary predicates ("is this value inside the allowed range/region") implemented with an operator/primitive that is off by the exact edge condition, and both are reused as the single source of truth elsewhere in the code (the `close` function reused the same boundary semantics as `notExpired`; `resolveWithin` is reused by `dispatcher.ts`, `app-store.ts`, and `copilot-conflict-context.ts` — I could not fully audit all of these additional call sites in the time available, so there may be more severely-impacted call paths, such as file writes, that also rely on this same weakened guarantee).

### Impact Explanation
The confirmed sink for this path (`dispatcher.ts` `openRepositoryFromUrl`) calls `shell.showItemInFolder(resolved)`, which reveals the existence and location of a file/folder outside the intended repository root to the file manager — an information-disclosure / sandbox-boundary violation triggered purely by clicking a link, with no local access, credentials, or malware required. This satisfies the "file read/write outside the repo, triggered by an attacker-controlled deep link" category in the given valid-impact list. The severity could be materially higher if any of the other, unaudited call sites of `resolveWithin` (`app-store.ts`, `copilot-conflict-context.ts`) use the resolved path for a write or execute operation rather than merely revealing it — that could not be confirmed within the available exploration budget and should be checked explicitly.

### Likelihood Explanation
Exploitation requires: (1) the victim has previously cloned or opens a repository whose local folder name is predictable/attacker-influenced (Desktop derives clone folder names from the remote's repo name, and an attacker fully controls the repo name of a repository they own), and (2) the victim clicks an "Open in Desktop" (`x-github-client://openRepo/...`) link with a crafted `filepath` parameter. Both conditions are plausible attacker-controlled inputs per the valid-impact scope (a link/deep link the user clicks). The main uncertainty is whether an attacker can reliably predict/pre-stage a sibling directory name that both (a) matches the victim's actual local clone path as a string prefix and (b) already contains a sensitive file to reveal — this makes real-world exploitation environment-dependent rather than universally reliable, but the underlying validation logic is still incorrect and should be fixed regardless.

### Recommendation
Change the containment check in `_resolveWithin` (`app/src/lib/path.ts`, line 71) from a raw prefix test to a boundary-aware comparison, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests for the sibling-directory case (root `/a/b`, candidate resolving to `/a/b-evil/...`) to prevent reintroduction, and audit all other callers of `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` (`app-store.ts`, `copilot-conflict-context.ts`) to confirm none of them perform a write/execute using a path validated only by the flawed check.

### Proof of Concept
1. Attacker creates a public GitHub repository named `victim-repo-evil` containing a file `secret.txt` inside it, and also owns/predicts that the victim has (or will) clone a different repository literally named `victim-repo` into the default GitHub Desktop clone directory (e.g. `~/Documents/GitHub/victim-repo`).
2. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/attacker/victim-repo?filepath=..%2Fvictim-repo-evil%2Fsecret.txt` (pointing `url` at the real `victim-repo` so Desktop opens the existing local clone, while `filepath` uses `..` to climb out of it).
3. Desktop parses the deep link via `parseAppURL`/`dispatchURLAction` and calls `openRepositoryFromUrl`, which calls `resolveWithin(repository.path /* .../victim-repo */, '../victim-repo-evil/secret.txt')`.
4. Inside `_resolveWithin`, `realResolved` becomes `.../GitHub/victim-repo-evil/secret.txt`, and `realRoot` is `.../GitHub/victim-repo`. `realResolved.startsWith(realRoot)` evaluates `true` because the string `.../GitHub/victim-repo-evil/secret.txt` literally begins with `.../GitHub/victim-repo`, even though `victim-repo-evil` is a sibling directory, not a subdirectory.
5. `resolveWithin` incorrectly returns the resolved sibling path instead of `null`, and `shell.showItemInFolder(resolved)` reveals `secret.txt` from outside the intended repository root — exactly the kind of boundary-check failure described in the seed report, applied to Desktop's path-containment invariant instead of Primitive's expiry invariant.

### Citations

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1972)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }

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
