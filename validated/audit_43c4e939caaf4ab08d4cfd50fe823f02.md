## Title
Directory-containment check in `resolveWithin` uses a raw prefix `startsWith` without a path-separator boundary, allowing a crafted deep-link filepath to escape the intended repository root - (File: `app/src/lib/path.ts`)

### Summary
The reported Solidity bug is a broken-invariant class: an equality/containment check is performed on a raw, unnormalized representation (`packedFloat.unwrap`) instead of on the semantically normalized value, so two values that are actually equal (or, by extension, two paths where one is *not* actually inside the other) are misjudged. The same broken-invariant class exists in GitHub Desktop's `_resolveWithin` helper, which is the guard used to decide whether a path derived from **untrusted, attacker-controlled input** (a deep-link `filepath`) is safely contained within a repository's root directory. The containment check is a bare string `startsWith(realRoot)` with no trailing path-separator boundary, so a sibling directory whose name has `realRoot` as a string prefix (e.g. `repo-evil` vs `repo`) is incorrectly treated as being "inside" `repo`.

### Finding Description
`_resolveWithin` in [1](#0-0)  resolves a set of path segments against a `rootPath` and is supposed to guarantee the result stays "at, or underneath" the root. The final safety decision is:
```
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

This is a plain string-prefix comparison. It does **not** verify that `realResolved` equals `realRoot`, nor that the byte immediately following the `realRoot` prefix in `realResolved` is a path separator. Consequently, if `realRoot` is e.g. `/Users/victim/Documents/GitHub/repo`, and the resolved/realpath'd candidate is `/Users/victim/Documents/GitHub/repo-secrets/leak.txt`, the check `"/Users/.../repo-secrets/leak.txt".startsWith("/Users/.../repo")` evaluates to `true` even though `repo-secrets` is a completely different, sibling directory that is not underneath `repo` at all.

This exported helper is used to sanitize `filepath` values coming from GitHub Desktop's `x-github-client://` / `github-desktop://` deep-link handler in `openRepositoryFromUrl`:
```
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
``` [3](#0-2) 

The only prior check is `isAbsolute(filepath)`, which blocks absolute paths but not relative traversal (`..`) segments that walk out of the repo and back into a sibling directory. `_resolveWithin` does correctly canonicalize `..` segments via `Path.resolve`/`realpath`, but the final boundary test is the flawed `startsWith` comparison, so a filepath like `../repo-secrets/leak.txt` (where `repo-secrets` is a sibling of the actual repository directory, e.g. another cloned repo, a backup folder, or any directory that happens to share `repository.path` as a string prefix) will pass the guard and be handed to `shell.showItemInFolder`, revealing/opening a file the user never consented to access outside the intended repository.

This mirrors the root cause of the Float128 report precisely: the code assumes a raw, unnormalized comparison (`unwrap(a) == unwrap(b)` in Solidity; `str.startsWith(prefix)` here) is equivalent to the semantically intended comparison ("packed floats represent the same numeric value"; "path is contained within root"), but the representation admits false positives that the check does not account for.

### Impact Explanation
An attacker who can get a victim to click a crafted GitHub Desktop deep link (`x-github-client://openRepo/...&filepath=...`) can cause Desktop to reveal/open a file located in a sibling directory of the targeted repository - i.e., outside the repository root that the containment check is meant to enforce. Depending on what sibling directories exist on the victim's machine at predictable/common locations (e.g. multiple cloned repos, backup copies, IDE workspace folders under the same parent), this can be used to disclose file existence/contents via `shell.showItemInFolder`/Explorer-Finder reveal outside the intended sandboxed root, i.e., "file read outside the repo" driven by "a link or deep link the user clicks," which is explicitly in scope per the Valid Impact criteria.

### Likelihood Explanation
Exploitation requires only that the victim click an attacker-supplied Desktop deep link containing a `filepath` parameter and that a sibling directory sharing the repository path as a prefix exist on disk - a realistic scenario for users who keep multiple related repositories (e.g. `repo`, `repo-fork`, `repo2`) under the same parent folder. No local access, elevated privileges, or pre-existing malware is required; the only user action needed is a normal, expected click on a GitHub-provided-looking link, consistent with the intended deep-link UX. The `isAbsolute` check gives a false sense of security while not blocking this relative-traversal-to-sibling-directory case.

### Recommendation
Fix the boundary check in `_resolveWithin` to require either exact equality or that the next character after the root prefix is the platform path separator, e.g.:
```
return (
  realResolved === realRoot ||
  realResolved.startsWith(realRoot + Path.sep)
) ? resolved : null
```
using the `options`-provided separator/join semantics (POSIX vs Win32) so the fix applies consistently to `resolveWithin`, `resolveWithinPosix`, and `resolveWithinWin32`.

### Proof of Concept
Given the existing test harness style in [4](#0-3) , add a case demonstrating the bypass:
```ts
it('incorrectly treats sibling directory as contained (prefix confusion)', async () => {
  // Assume two sibling directories: /tmp/xyz/repo and /tmp/xyz/repo-secrets
  const root = '/tmp/xyz/repo'
  const sibling = '/tmp/xyz/repo-secrets/leak.txt'
  // Simulate the flawed final check directly:
  assert(sibling.startsWith(root)) // true, but sibling is NOT inside root
})
```
In the real flow, a deep link such as `x-github-client://openRepo/https://github.com/foo/repo?filepath=..%2Frepo-secrets%2Fleak.txt` reaches `openRepositoryFromUrl` in [5](#0-4) , passes the `isAbsolute` check (it's relative), and `resolveWithin(repository.path, filepath)` returns a non-null resolved path because `realResolved.startsWith(realRoot)` is satisfied by the sibling directory name, causing `shell.showItemInFolder` to reveal a file outside the repository.

### Citations

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
