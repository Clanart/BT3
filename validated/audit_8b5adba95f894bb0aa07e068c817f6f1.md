## Finding: Path-boundary check in `resolveWithin` uses unanchored `String.startsWith`, allowing sibling-directory escape

The RubyGems report's root cause was `destination_dir` being used to gate a path via `install_location`'s bare `start_with?` comparison — a check that treats `/tmp/install-whatever` as "inside" `/tmp/install` because the prefix happens to match textually with no separator boundary. The exact same flaw exists in GitHub Desktop's own path-containment primitive.

### Title
Path-containment check in `resolveWithin` accepts sibling directories/files whose names share a prefix with the root, enabling path-traversal escape via deep links - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin` (and its `resolveWithinPosix`/`resolveWithinWin32` variants) is the app's shared utility for guaranteeing "this path stays inside that root directory." Its final containment check is:

```ts
return realResolved.startsWith(realRoot) ? resolved : null
``` [1](#0-0) 

This is a bare string-prefix test with no trailing separator boundary — the same broken invariant as the RubyGems `install_location` bug. `'/Users/victim/Documents/GitHub/repo-secrets/token.txt'.startsWith('/Users/victim/Documents/GitHub/repo')` evaluates to `true`, even though `repo-secrets` is a sibling of `repo`, not a descendant.

### Finding Description
`resolveWithin` is documented as guaranteeing the resolved path "reside[s] at, or underneath" `rootPath`: [2](#0-1) 

It builds `resolved` via `Path.resolve(normalizedRoot, normalizedRelative)`, then calls `realpath` on both root and resolved path, and finally checks containment with an unanchored `startsWith`: [3](#0-2) 

Because `startsWith` performs a literal-character prefix match, any resolved path whose string representation happens to start with `realRoot`'s characters passes the check — regardless of whether a path separator actually separates the two. For example, with `rootPath = '/Users/victim/Documents/GitHub/repo'` and `pathSegments = ['..', 'repo-secrets', 'id_rsa']`, `resolve()` produces `/Users/victim/Documents/GitHub/repo-secrets/id_rsa`, which is a sibling directory outside `repo`, yet `'/Users/victim/Documents/GitHub/repo-secrets/id_rsa'.startsWith('/Users/victim/Documents/GitHub/repo')` is `true`, so the traversal is allowed through.

The unit tests for this function only cover exact `..`/symlink escapes and never test the sibling-prefix case, so this gap is unpatched and untested: [4](#0-3) 

This primitive is used to gate attacker-influenced paths in at least these call sites:
- `app/src/ui/dispatcher/dispatcher.ts` — `openRepositoryFromUrl`, which resolves the `filepath` parameter of an `x-github-client://openRepo` deep link against `repository.path` before calling `shell.showItemInFolder`: [5](#0-4) 
- `app/src/lib/copilot-conflict-context.ts`, which resolves conflicted-file paths (attacker-influenced via a crafted merge) against the repo working directory before reading file contents: [6](#0-5) 
- `app/src/lib/stores/app-store.ts` also calls `resolveWithin` twice for similar containment checks.

The `filepath` query parameter is taken directly, unsanitized for traversal segments or prefix collisions, from an attacker-crafted deep link URL: [7](#0-6) 

Note that `Path.resolve`/`normalize` do collapse `..` segments before the check runs, so a plain `../secret` would already have been folded into an absolute path pointing outside root — and it is precisely *this* folded, out-of-root path that the flawed `startsWith` may still accept, provided the target directory name happens to share the root directory's name as a literal prefix (a condition realistically satisfiable when repository directories are commonly named things like `myapp`, `myapp-private`, `myapp.bak`, cloned side-by-side in the same parent folder, which is the default GitHub Desktop clone layout).

### Impact Explanation
If a directory or file adjacent to the repository shares a name prefix with the repository folder (e.g., `repo` and `repo-secrets`, or `project` and `project.bak`), a maliciously crafted `x-github-client://openRepo?url=...&filepath=../repo-secrets/<file>` deep link can cause `resolveWithin` to incorrectly validate a path that is actually outside the cloned repository as "contained," resulting in the disclosed/opened file being outside the user's expected repo boundary (e.g., `shell.showItemInFolder` revealing a sibling-directory file, or in the Copilot conflict-context path, reading file contents outside the working tree). This matches the report's class of "installer/consumer trusts a prefix check instead of a proper boundary check," letting an attacker step from "restricted to X" to "anything named `X*`."

### Likelihood Explanation
Exploitation requires: (1) the victim clicking an attacker-supplied `x-github-client://` deep link (a normal, low-friction social step already considered in-scope per the Valid Impact criteria — "a link or deep link the user clicks"), and (2) an adjacent directory/file existing whose name is a superstring of the repository directory name. Because GitHub Desktop's default clone root places all repos as siblings under one base directory (see `app/src/lib/git/clone.ts`'s sensitive-path guard operating at the same directory level), same-parent sibling directories with overlapping name prefixes are a common real-world layout, making this more than a theoretical edge case.

### Recommendation
Fix the containment check to enforce a real path-separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Apply the equivalent fix to all three variants (`resolveWithin`, `resolveWithinPosix`, `resolveWithinWin32`) using the corresponding `options.sep`/separator, and add regression tests asserting that a sibling directory sharing a name prefix (e.g., root `.../repo` vs. target `.../repo-evil/file`) is rejected.

### Proof of Concept
```ts
import { resolveWithin } from './app/src/lib/path'
import { mkdirSync, writeFileSync } from 'fs'
import { join } from 'path'

// Simulate two sibling repo clones under the same parent directory
mkdirSync('/tmp/gh/repo', { recursive: true })
mkdirSync('/tmp/gh/repo-secrets', { recursive: true })
writeFileSync('/tmp/gh/repo-secrets/token.txt', 'super-secret-token')

const result = await resolveWithin('/tmp/gh/repo', '../repo-secrets/token.txt')
console.log(result)
// -> '/tmp/gh/repo-secrets/token.txt'  (WRONG: should be null, this is outside root)
```
This mirrors `dispatcher.ts`'s `openRepositoryFromUrl`, where an attacker-supplied deep link `x-github-client://openRepo?url=<victim-repo-url>&filepath=../repo-secrets/token.txt` would cause `resolveWithin(repository.path, filepath)` to return the out-of-root path and invoke `shell.showItemInFolder` on it instead of being rejected. [8](#0-7)

### Citations

**File:** app/src/lib/path.ts (L13-28)
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
```

**File:** app/src/lib/path.ts (L64-72)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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

**File:** app/src/lib/copilot-conflict-context.ts (L390-407)
```typescript
      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }
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
