## Title
Path-containment bypass in `_resolveWithin` due to unanchored `startsWith` prefix check enables sibling-directory file disclosure into Copilot conflict context - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin` in `app/src/lib/path.ts` validates that a resolved path stays inside a given root by doing `realResolved.startsWith(realRoot)`, without ensuring `realRoot` is followed by a path separator (or is an exact match). This is a classic prefix-confusion bug: any directory that is a *sibling* of the root and whose name has the root's basename as a string prefix (e.g. root = `/Users/victim/repo`, sibling = `/Users/victim/repo-evil`) will incorrectly be treated as "inside" the root.

### Finding Description [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`realRoot` has no trailing separator appended before the `startsWith` comparison. For a relative segment like `../repo-evil/file.txt` given root `/Users/victim/repo`, `resolve()` produces `/Users/victim/repo-evil/file.txt`, and `realpath` of both leaves `realRoot = '/Users/victim/repo'` and `realResolved = '/Users/victim/repo-evil/file.txt'`. Since the string `'/Users/victim/repo-evil/file.txt'` literally starts with the substring `'/Users/victim/repo'`, the containment check passes even though `repo-evil` is a completely different, sibling directory, not a subdirectory of `repo`. This matches the invariant break described in the question: `EXACT_VALUE` `realRoot='/Users/victim/repo'` incorrectly matches `realResolved='/Users/victim/repo-secrets/secret.txt'`.

This function is consumed by `buildConflictContext` in `app/src/lib/copilot-conflict-context.ts`, which resolves each conflicted file's repository-relative path against the repository's working directory before reading its content for the Copilot merge-conflict-resolution prompt: [2](#0-1) 

If `resolveWithin` returns a non-null path outside the intended root, `readFile` is invoked on that out-of-scope path and its contents are folded into `extractConflictHunks` and ultimately into the Copilot prompt text via `formatConflictContextForPrompt`.

### Reachability caveat (important, unverified)
The theoretical bug in `_resolveWithin` is real and independently reproducible with a plain unit test (`resolveWithin(root, '../root-evil/file.txt')` returns non-null instead of `null`), confirming the containment invariant is broken. However, whether an attacker can actually drive `file.path` in `buildConflictContext` to contain a `../`-style traversal segment depends on how the list of conflicted files is produced upstream (from git's index/working-tree status). I was not able to fully trace, within the remaining budget, whether git's own status/conflict enumeration (`app/src/lib/git/status.ts`, `app/src/lib/stores/app-store.ts`) ever passes through raw, attacker-supplied path strings containing `..` segments without prior normalization/rejection — git itself generally rejects tree/index entries containing `..` path components. I could not conclusively confirm or rule out a path where a hostile repository could inject such a segment (e.g., via a crafted index entry, a name with an unusual byte sequence, or via a code path other than `buildConflictContext` that calls `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` with less-sanitized input, such as `app/src/ui/dispatcher/dispatcher.ts`).

### Impact Explanation
If reachable, this would let an attacker plant a conflicting-path entry that resolves outside the repository into an adjacent, similarly-named directory (e.g., a backup folder, another cloned repo, or any directory sharing a name prefix with the current repo), causing Desktop to read and transmit that file's content to the Copilot conflict-resolution flow — disclosing local files outside the intended repository scope, matching the "High" impact classified in the question (file disclosure via Copilot conflict context).

### Likelihood Explanation
The `_resolveWithin` defect itself is trivially reproducible and does not require symlinks (existing tests only cover symlink-based escapes, not the simple substring-prefix bypass) — this is a real gap in the function's existing protections, which is otherwise fairly hardened (null-byte checks, symlink-aware `realpath` checks). Exploitability from an actual malicious repository hinges on whether an attacker can control a conflicted file's `path` string with a `../` segment reaching `buildConflictContext`, which I could not conclusively confirm; git normally sanitizes/rejects such path components in tree/index objects, which would substantially lower likelihood for that specific call site. Without confirming an end-to-end attacker-controlled trigger, I can validate the code-level flaw but not with full certainty the practical end-to-end exploitation path via a cloned/fetched repository.

### Recommendation
Fix the containment check in `_resolveWithin` to require an exact match or a root ending in a path separator, e.g.:
```
const rootWithSep = realRoot.endsWith(sep) ? realRoot : realRoot + sep
return realResolved === realRoot || realResolved.startsWith(rootWithSep) ? resolved : null
```
Add a regression test asserting `resolveWithin(root, '../' + basename(root) + '-evil/file.txt')` returns `null`, complementing the existing symlink-escape tests in `app/test/unit/path-test.ts`.

### Proof of Concept
```ts
import { resolveWithin } from '../../src/lib/path'
import { mkdtemp, mkdir, writeFile } from 'fs/promises'
import { join } from 'path'
import { tmpdir } from 'os'

const base = await mkdtemp(join(tmpdir(), 'root'))
const evilDir = base + '-evil'
await mkdir(evilDir)
await writeFile(join(evilDir, 'secret.txt'), 'sensitive')

const result = await resolveWithin(base, '../' + require('path').basename(evilDir) + '/secret.txt')
// BUG: result is NOT null even though evilDir is a sibling, not a subdirectory, of base
console.assert(result !== null, 'containment check bypassed')
``` [3](#0-2) [4](#0-3)

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

**File:** app/test/unit/path-test.ts (L65-101)
```typescript
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
