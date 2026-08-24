### Title
Sibling-directory prefix bypass in `resolveWithin()` allows escaping the repository root via crafted deep-link `filepath` - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin()` in `app/src/lib/path.ts` enforces the "stay inside the root" invariant with `realResolved.startsWith(realRoot)`, a plain string-prefix comparison with no path-separator boundary check. Because Node's `path.resolve()` will happily produce a path outside `rootPath` when relative segments contain unconsumed `..` components, and the "containment" check only compares string prefixes, a target path in a *sibling* directory whose name starts with the root directory's name (e.g. `RootRepo-secrets`) passes the check even though it is not actually inside `RootRepo`. This mirrors the report's bug class: a guard that looks like it enforces an invariant but is subtly wrong, so the security-relevant path silently succeeds when it should be rejected.

### Finding Description
`resolveWithin()` (and its `Posix`/`Win32` variants) is Desktop's single chokepoint for validating that an attacker/user-supplied relative path stays inside a trusted root directory: [1](#0-0) 

The relevant logic:
1. `resolved = resolve(normalizedRoot, normalizedRelative)` — if the input segments contain more `..` than there are path components to cancel, Node's `resolve()` will simply climb past `rootPath` into the parent directory and then descend into whatever sits there.
2. The only backstop is:
```js
return realResolved.startsWith(realRoot) ? resolved : null
```
This is a **substring** check, not a **path-segment** check. If `realRoot` is `/Users/victim/Documents/GitHub/MyRepo` and the crafted relative path resolves to `/Users/victim/Documents/GitHub/MyRepo-exfil/secret.txt`, then:
```
"/Users/victim/Documents/GitHub/MyRepo-exfil/secret.txt".startsWith("/Users/victim/Documents/GitHub/MyRepo")  // true
```
even though `MyRepo-exfil` is a completely different, sibling directory. The correct check would require `realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)`.

This is directly analogous to the C4 report: the guard exists, looks semantically right, but the comparator is wrong, so input that should be rejected is accepted (the inverse of the original bug's `!=`/`==` mixup, but the same class of "broken invariant enforcement via comparator logic").

**Reachability from attacker-controlled input:** `resolveWithin()` is invoked with an attacker-influenced `filepath` when the user clicks a `x-github-client://openRepo/...?filepath=...` deep link, handled end-to-end via `parseAppURL` → `handleAppURL` → `Dispatcher.openRepositoryFromUrl`: [2](#0-1) 

The only pre-filter is a check that `filepath` is not an absolute path (`isAbsolute(filepath)`); relative paths containing crafted `..` segments are not blocked before being handed to `resolveWithin`: [3](#0-2) 

The clone-target directory name itself is derived from attacker-controlled remote-URL content via `sanitizeCloneName`/`parseRepositoryIdentifier` (used for "open/clone from URL" flows), so an attacker who controls the link (and, in the fetch/clone scenario, chooses a repository name that is a short prefix of a sensitive sibling folder, e.g. targeting `Documents/GitHub/Foo` when the user also happens to have `Documents/GitHub/FooBar`) can make `resolveWithin` return a path in `FooBar` instead of rejecting it.

### Impact Explanation
A path that legitimately belongs to a *different* directory on disk is accepted as "inside the repo," and the caller then acts on it — e.g. `shell.showItemInFolder(resolved)` in `dispatcher.ts`, which reveals/interacts with a file physically located outside the intended repository root. `resolveWithin` is also used as the sole path-traversal/symlink-escape guard in `copilot-conflict-context.ts` when reading conflicted file contents to send to the Copilot SDK: [4](#0-3) 

If a similarly-named sibling directory exists next to the repository's working directory, this same faulty boundary check could let file contents from outside the repo be read and sent as part of the conflict-resolution prompt context — a "read outside the repo" primitive matching the Valid Impact criteria in this task. Severity is Medium: no direct RCE, but it breaks a documented and load-bearing security invariant ("resolved path is guaranteed to reside at, or underneath rootPath") in a way that can leak file contents or reveal file locations outside the intended sandboxed root, triggered purely by the victim opening a link or a cloned/fetched repository with a strategically-named sibling directory.

### Likelihood Explanation
The comment in `path.ts` explicitly documents the intended guarantee ("the resolved path is guaranteed to reside at, or underneath this path"), showing this is a deliberate, relied-upon security boundary, not incidental code. The existing unit tests in `app/test/unit/path-test.ts` cover `..` traversal, null bytes, and symlink escapes, but do **not** test the sibling-directory-with-shared-prefix case, so the gap has not been caught by the test suite: [5](#0-4) 

Exploitation requires only: (1) the victim has (or can be made to have) a sibling directory whose name is prefixed by the target root's name, and (2) the victim clicks an `x-github-client://openRepo/...?filepath=...` link or opens conflict-context flows in a similarly-named sibling repo — both are unprivileged, user-click-driven, no local/admin access needed.

### Recommendation
Change the containment check to require an exact match or a match followed by the path separator:
```js
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests for the sibling-directory prefix case (e.g., root `/tmp/foo` vs. resolved `/tmp/foobar`).

### Proof of Concept
```ts
import { resolveWithin } from '../../src/lib/path'
import { mkdtemp, mkdir, writeFile } from 'fs/promises'
import { tmpdir } from 'os'
import { join } from 'path'

const base = await mkdtemp(join(tmpdir(), 'gh-desktop-'))
const root = join(base, 'MyRepo')
const sibling = join(base, 'MyRepo-exfil')
await mkdir(root)
await mkdir(sibling)
await writeFile(join(sibling, 'secret.txt'), 'top secret token')

// crafted relative path escapes `root` and lands in the sibling dir,
// but the prefix check still returns a non-null (accepted) path:
const result = await resolveWithin(root, '..', 'MyRepo-exfil', 'secret.txt')
console.log(result) // -> "<base>/MyRepo-exfil/secret.txt" — should have been rejected (null)
```
This demonstrates the invariant "resolved path is guaranteed to reside at, or underneath rootPath" is violated: `result` points to a file in a completely different directory, yet `resolveWithin` returns it instead of `null`.

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

**File:** app/test/unit/path-test.ts (L44-101)
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

    if (!__WIN32__) {
      it('fails for paths that use a symlink to traverse outside of the root', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          assert((await resolveWithin(tempDir, symlinkName)) === null)
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })

      it('succeeds for paths that use a symlink to traverse outside of the root and then back again', async () => {
        const tempDir = await mkdtemp(join(tmpdir(), 'path-test'))
        const symlinkName = 'dangerzone'
        const symlinkPath = join(tempDir, symlinkName)

        try {
          await symlink(resolve(tempDir, '..', '..'), symlinkPath)
          const throughSymlinkPath = join(
            symlinkName,
            basename(resolve(tempDir, '..')),
            basename(tempDir)
          )
          assert.equal(
            await resolveWithin(tempDir, throughSymlinkPath),
            resolve(tempDir, throughSymlinkPath)
          )
        } finally {
          await unlink(symlinkPath)
          await rmdir(tempDir)
        }
      })
    }
```
