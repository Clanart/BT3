## Title
Path-prefix boundary bypass in `resolveWithin` allows sibling-directory disclosure via string-prefix collision - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin` in `app/src/lib/path.ts` validates that a resolved path stays within a root directory using `realResolved.startsWith(realRoot)`, with no check for a path-separator boundary or exact equality after the prefix. Since Node.js path strings do not have a trailing separator, this allows a sibling directory whose name is a superstring of the root directory name (e.g. `/tmp/repo-secret` vs root `/tmp/repo`) to pass the containment check, even though it is not actually nested under the root.

### Finding Description [1](#0-0) 

```js
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`String.prototype.startsWith` performs a pure character-prefix comparison. If `realRoot` is `/tmp/repo` and an attacker-influenced relative segment resolves (after `realpath`) to `/tmp/repo-secret/f`, the check `'/tmp/repo-secret/f'.startsWith('/tmp/repo')` evaluates to `true`, because the literal string `/tmp/repo` is a prefix of `/tmp/repo-secret/f`. The correct containment check needs to additionally verify that the character immediately following `realRoot` in `realResolved` is the path separator (or that `realResolved === realRoot`). This missing boundary check is present in all three exported wrappers (`resolveWithin`, `resolveWithinPosix`, `resolveWithinWin32`) since they all funnel through `_resolveWithin`. [2](#0-1) 

The existing unit tests only cover directory-traversal (`../..`) and symlink-escape cases, not the sibling-prefix collision case, so this gap is untested. [3](#0-2) 

This function is consumed by `buildConflictContext` in `app/src/lib/copilot-conflict-context.ts`, which calls `resolveWithin(workingDirectory, file.path)` for each conflicted file path and, if it resolves non-null, reads the file's full contents into `rawContent`, which is later included in the Copilot prompt: [4](#0-3) 

### Impact Explanation
If a `file.path` value can be made to resolve (after `join`/`normalize`/`resolve` and `realpath`) to a directory outside the repository root but sharing the root directory name as a string prefix (e.g. repository cloned to `/home/user/repo` next to `/home/user/repo-secrets`), the containment check would incorrectly accept it, and the file's contents would be read via `readFile(absolutePath, 'utf8')` and placed into `rawContent`/hunks, potentially forwarded to the Copilot SDK as conflict context — disclosing local files outside the selected repository. This matches the general risk pattern described in the "Scope" (attacker-controlled repository content causing Desktop to read/transmit files outside the selected repository).

### Likelihood Explanation
The `_resolveWithin` boundary flaw itself is real and demonstrable in isolation (confirmed by code inspection above; no equality/separator check exists after `startsWith`). However, I could not fully verify, within the available tooling, whether `file.path` values reaching `buildConflictContext` can actually be attacker-crafted to contain `..`-style traversal segments. Git tree/index entries generally cannot contain path components like `..` or absolute paths, so the conflicted file list surfaced by `git status`/merge machinery is typically constrained to paths within the repository tree, which would make the primary exploitation vector (crafting a `file.path` traversal string like `../repo-secret/f`) hard to trigger through ordinary repository content alone. I was not able to trace, within this pass, the full call chain from `app-store.ts`'s conflict-file gathering back to raw git output to conclusively rule out or confirm an attacker-controllable path string reaching `resolveWithin` with a traversal segment. This is a gap in my verification, not a confirmed non-issue.

### Recommendation
Fix the boundary check in `_resolveWithin` (`app/src/lib/path.ts`) to require an exact match or a following path separator, e.g.:
```js
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test mirroring the PoC below (sibling directory with a prefix-colliding name) to `app/test/unit/path-test.ts`.

### Proof of Concept
```js
import { mkdtemp, mkdir, writeFile, rmdir, unlink } from 'fs/promises'
import { join, basename } from 'path'
import { tmpdir } from 'os'
import { resolveWithin } from '../../src/lib/path'

const root = await mkdtemp(join(tmpdir(), 'repo-'))       // e.g. /tmp/repo-abc123
const sibling = `${root}-secret`                            // e.g. /tmp/repo-abc123-secret
await mkdir(sibling)
await writeFile(join(sibling, 'f'), 'top-secret-content')

const result = await resolveWithin(root, join('..', basename(sibling), 'f'))
// EXPECTED: null (sibling is outside root)
// ACTUAL: non-null path pointing at /tmp/repo-abc123-secret/f
console.log(result)

await unlink(join(sibling, 'f'))
await rmdir(sibling)
await rmdir(root)
```

This demonstrates that `resolveWithin` (backed by the flawed `_resolveWithin` prefix check) fails to reject a path that resolves to a sibling directory outside the intended root, confirming the boundary-check defect described in the question. Whether this is reachable end-to-end through `copilot-conflict-context.ts`'s conflicted-file path handling with attacker-controlled repository content remains unconfirmed pending further tracing of how `file.path` values are produced upstream.

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

**File:** app/test/unit/path-test.ts (L44-102)
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
  })
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-460)
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

      // Guard against reading pathologically large files into memory. This is
      // a memory-safety bound only — resolvability is decided from the conflict
      // hunks below, not the whole-file size.
      try {
        const fileStat = await stat(absolutePath)
        if (fileStat.size > MAX_CONFLICT_FILE_READ_SIZE) {
          return {
            path: file.path,
            hunks: [],
            skippedReason: 'File too large to resolve automatically',
          }
        }
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      let content: string
      try {
        content = await readFile(absolutePath, 'utf8')
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      const hunks = extractConflictHunks(content)
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }

      // Gate on the size of the conflict content we'd actually send to the
      // model, not the whole-file size.
      const hunkSkipReason = getHunkSkipReason(hunks)
      if (hunkSkipReason !== null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: hunkSkipReason,
        }
      }

      return { path: file.path, hunks, rawContent: content }
```
