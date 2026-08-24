### Title
`resolveWithin()` sandbox check uses unanchored `startsWith`, allowing deep-link `filepath` to reveal files outside the repository - ([File: app/src/lib/path.ts])

### Summary
`_resolveWithin()` is the app's sole primitive for confining a user/attacker-influenced relative path to a "root" directory (analogous to a totalSupply-style invariant check, but here the invariant is "resolved path is *inside* root"). The final containment check, `realResolved.startsWith(realRoot)`, is a bare string-prefix comparison with no path-separator boundary check. Just like the Canto `require(balance == _initialSupply)` bug compared against the wrong reference value and let an unintended state through, this check compares against the *string* `realRoot` instead of `realRoot + path separator`, so any sibling directory whose name happens to share `realRoot` as a prefix (e.g. `repo` vs `repo-secrets`) passes the "within root" test even though it is not actually inside the root.

### Finding Description
`app/src/lib/path.ts`:
```ts
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)

return realResolved.startsWith(realRoot) ? resolved : null
``` [1](#0-0) 

There is no check that `realResolved === realRoot` or that the character following `realRoot` in `realResolved` is a path separator. Consequently `"/Users/victim/Documents/GitHub/repo-secrets/token.txt".startsWith("/Users/victim/Documents/GitHub/repo")` evaluates to `true`, even though `repo-secrets` is a sibling directory of `repo`, not a subdirectory.

This function is consumed directly in the app's deep-link ("Open Repository from URL") handler in `dispatcher.ts`:
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
    log.error(
      `Prevented attempt to open path outside of the repository root: ${filepath}`
    )
  }
}
``` [2](#0-1) 

`filepath` originates from the `x-github-client://openRepo/...?filepath=...` custom-protocol deep link, which is fully attacker-controlled content that a victim can be lured into clicking (a GitHub PR link, a malicious website, etc.), matching the "link/deep link the user clicks" attacker primitive in scope. The only defense against traversal is the `isAbsolute()` check, which blocks absolute paths but does nothing against a relative traversal like `../repo-secrets/id_rsa` — the existing `resolveWithin` boundary check is exactly the safeguard that's supposed to catch this, and it fails to do so because of the unanchored prefix comparison.

The existing test suite for `resolveWithin` in `app/test/unit/path-test.ts` only verifies the "traverse out and back into the *same* root" case and a symlink-escape case; it never tests the "sibling directory sharing the root's name as a string prefix" case, so this specific bypass is not covered: [3](#0-2) 

The same `resolveWithin` primitive is also relied on elsewhere (`app/src/lib/stores/app-store.ts`, `app/src/lib/copilot-conflict-context.ts`), so the flawed invariant is not confined to a single call site.

### Impact Explanation
An attacker who gets a victim to open a crafted `x-github-client://openRepo/...` deep link (e.g., embedded in a webpage, chat message, or malicious repository README) can cause Desktop to call `shell.showItemInFolder()` on a file *outside* the intended repository, provided the victim has any sibling directory on disk whose name is prefixed by the repository's directory name (a very common scenario for developers who keep multiple related clones, e.g. `myproject` and `myproject-secrets`, or `repo` and `repo.bak`). This can reveal/point to files containing credentials, SSH keys, or other repo-external content the victim did not intend to expose via a link click — no local access, prior malware, or leaked credentials are required, only clicking a link, matching the required "Valid Impact" bar (file read outside the repo via a link the user clicks).

### Likelihood Explanation
Exploitability depends on the victim having such a same-prefixed sibling directory, which is a plausible but not universal condition (severity should be scoped accordingly — likely Medium rather than High, since it needs a specific directory-naming coincidence on the victim's machine, but attackers can partially control this by choosing which repository/deep-link name to target if they know or can guess the victim's folder layout, e.g. targeting a commonly-named repo like `desktop` next to `desktop-internal`).

### Recommendation
Change the containment check in `_resolveWithin` (`app/src/lib/path.ts`) to require an exact match or a path-separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test covering the sibling-prefix case (e.g. root `.../repo` and target `.../repo-evil/file`) to `app/test/unit/path-test.ts`.

### Proof of Concept
1. Victim has two directories on disk: `/Users/victim/Documents/GitHub/repo` (a Desktop-tracked repository) and `/Users/victim/Documents/GitHub/repo-secrets/id_rsa` (any unrelated directory/file that happens to share the prefix).
2. Attacker sends victim a link: `x-github-client://openRepo/https://github.com/owner/repo?filepath=..%2Frepo-secrets%2Fid_rsa`.
3. Victim clicks it; Desktop parses the URL via `parseAppURL`, resolves/opens the `repo` repository, then calls `resolveWithin(repository.path, '../repo-secrets/id_rsa')`.
4. `resolve(normalizedRoot, normalizedRelative)` yields `/Users/victim/Documents/GitHub/repo-secrets/id_rsa`; `realpath` resolves it to the same value; `realResolved.startsWith(realRoot)` is `true` because `"...repo-secrets/id_rsa"` starts with the string `"...repo"`.
5. `resolveWithin` returns the out-of-root path instead of `null`, and `shell.showItemInFolder(resolved)` reveals `id_rsa` in Finder/Explorer — a file entirely outside the repository the deep link nominally targeted.

### Citations

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
