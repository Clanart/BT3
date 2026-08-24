Based on my analysis, I found a strong analog: a boundary-check bug in the path-containment guard `resolveWithin`, which mirrors the reported bug class (a range/containment check that fails to enforce the actual boundary, letting a value "escape" past the intended limit). [1](#0-0) 

### Title
Path-containment check in `resolveWithin` uses prefix `startsWith` without a path-separator boundary, allowing traversal into sibling directories - (File: app/src/lib/path.ts)

### Summary
`resolveWithin()` is Desktop's shared guard for confining an attacker-influenced relative path to a root directory before using it for file reads (Copilot conflict resolution) or file-reveal actions (deep-link `filepath` handling). Its final containment check is a raw string `startsWith` test between the resolved real path and the real root path, with no separator/equality boundary. This is the same class of defect as the reported `calculateMaxAllocation` bug: a comparison meant to enforce "must be inside/at-or-below X" instead only checks a weaker condition (string prefix) that a crafted input can satisfy while still being logically outside the intended boundary.

### Finding Description
`_resolveWithin` computes the real, symlink-resolved root and target paths and then decides containment with:

```ts
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)

return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

`String.prototype.startsWith` performs a literal character-prefix comparison with no notion of path segments. If the repository root is, e.g., `/Users/victim/Documents/GitHub/repo`, then a sibling directory `/Users/victim/Documents/GitHub/repo-secrets` also "starts with" that exact string, because `"repo-secrets"` begins with the characters `"repo"`. A relative path such as `../repo-secrets/secret.txt`, once joined and resolved against the root, produces a `realResolved` value that passes the check even though it is a completely different directory outside the intended root — the missing separator (`realRoot + Path.sep`) or exact-equality check is the guard that "does not stop the path."

The existing test suite only covers directory-traversal-then-back-in and symlink escape cases; it does not cover the sibling-prefix case, so this specific bypass is not exercised: [3](#0-2) 

`resolveWithin` is relied upon as the sole traversal guard in two attacker-reachable call sites:

1. Copilot merge-conflict context builder, which reads full file contents for paths taken directly from the (attacker-influenced) conflicted file list of a cloned/fetched repository: [4](#0-3) 

2. The `x-github-client://` deep-link handler, which takes a `filepath` argument from a URL the user clicks and reveals it in the file system if it resolves inside the repo: [5](#0-4) 

### Impact Explanation
Both call sites treat "resolves inside root" as license to act on the file: read arbitrary file content into an AI request payload (`buildConflictContext`) or reveal a file's existence/location outside the repo via `shell.showItemInFolder` (deep link path). This matches the "Valid Impact" bar: the attacker controls a cloned/fetched repository's file paths (conflict scenario) or a deep link the user clicks (`filepath` scenario), and the broken invariant is that a path check meant to be "at or under root" instead permits any sibling path whose name has the root's directory name as a string prefix — enabling reads or disclosures of files outside the intended repository boundary without any local/physical access or prior compromise.

### Likelihood Explanation
Exploitation only requires an attacker to know (or guess) the local clone directory name convention (commonly `<owner>/<repo>` cloned as `.../GitHub/<repo>`) and craft a conflicting file's relative path or a deep-link `filepath` value like `../<repo>-something/<target>`, relying on a plausible sibling directory (e.g., another clone, a build cache, or a backup folder with a name that happens to share the root's prefix). This is a real but conditional bypass — it depends on the existence of a same-prefixed sibling path on the victim's machine — so likelihood is moderate rather than trivial, but the code path itself has no additional defense once that condition is met.

### Recommendation
Change the containment check in `_resolveWithin` (app/src/lib/path.ts) to require an exact match or a properly delimited prefix, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(using the appropriate `sep` for the `options` variant — `Path.win32.sep`/`Path.posix.sep`). Add regression tests asserting that a sibling directory sharing the root's name as a string prefix (but not a true subdirectory) is rejected.

### Proof of Concept
```ts
import { resolveWithin } from '../../src/lib/path'
import { mkdir, writeFile, rmdir, unlink } from 'fs/promises'
import { join } from 'path'
import { tmpdir } from 'os'

// Simulate root = <tmp>/repo and sibling = <tmp>/repo-secrets
const base = await mkdtemp(join(tmpdir(), 'poc-'))
const root = join(base, 'repo')
const sibling = join(base, 'repo-secrets')
await mkdir(root)
await mkdir(sibling)
await writeFile(join(sibling, 'secret.txt'), 'TOP SECRET')

// Attacker-controlled relative path taken from a conflicted file entry
// or a `filepath` deep-link parameter.
const result = await resolveWithin(root, '../repo-secrets/secret.txt')

// BUG: result is NOT null even though the resolved path is outside `root`.
console.log(result) // => "<base>/repo-secrets/secret.txt"
```

### Citations

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
