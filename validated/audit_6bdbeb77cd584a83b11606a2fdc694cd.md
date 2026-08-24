### Title
Path-boundary check in `resolveWithin` uses unanchored `startsWith`, allowing deep-link `filepath` traversal to sibling directories - (File: `app/src/lib/path.ts`)

### Summary
The core containment check used across GitHub Desktop to guarantee that a resolved path stays inside a given root directory is implemented with a raw string `startsWith` comparison that is missing a path-separator boundary check. This mirrors the reported bug class: a security threshold/boundary comparison is computed against the wrong operand shape, so it looks correct but actually accepts values it should reject. Here, instead of a collateral-vs-debt threshold being weakened, a directory-containment threshold is weakened, letting a resolved path "leak" into a sibling directory whose name merely starts with the same prefix as the trusted root.

### Finding Description
`_resolveWithin` in [1](#0-0)  computes the real, symlink-resolved paths of both the root and the candidate, then does:

```
return realResolved.startsWith(realRoot) ? resolved : null
```

`String.prototype.startsWith` performs a prefix match with no separator awareness. If `realRoot` is `/Users/victim/Documents/GitHub/myrepo` and `realResolved` is `/Users/victim/Documents/GitHub/myrepo-secrets/id.txt`, the check passes even though `myrepo-secrets` is a completely different directory that merely shares the `myrepo` prefix. The invariant the function's own doc-comment promises — "the resolved path is guaranteed to reside at, or underneath this path" — is broken exactly the same way the reported contract bug broke its collateral invariant: the comparison operand boundary is off, silently widening what is accepted.

This function is the single point of truth used to defend against path traversal / symlink escape in at least two attacker-reachable call sites:
- `Dispatcher.openRepositoryFromUrl`, which handles the `filepath` parameter of the `x-github-client://openRepo` deep link and calls `resolveWithin(repository.path, filepath)` before calling `shell.showItemInFolder(resolved)`. [2](#0-1) 
- `buildConflictContext`, which resolves conflicted-file paths against the repository working directory before reading file content into the Copilot conflict-resolution prompt. [3](#0-2) 

The existing unit tests only cover exact-root traversal ("`..`" back to root) and symlink escape to `..`/`../..`; they never test the sibling-prefix case, so the gap is unguarded. [4](#0-3) 

### Impact Explanation
Via the `x-github-client://openRepo?url=...&filepath=...` deep link (which a user can be lured to click from any web page), an attacker can supply a `filepath` such as `../<reponame>-something/secret.txt`. If the victim has any other locally cloned directory under the same parent folder whose name happens to start with the target repository's directory name (a very common pattern — forks, `-old`, `-backup`, `.wiki`, numbered clones, etc.), `resolveWithin` will incorrectly treat that sibling path as "inside" the repository and `shell.showItemInFolder` will open the OS file explorer pointed at a file outside the intended repository — a containment/read-outside-repo violation of exactly the class flagged as valid impact (file read outside the repo via an attacker-controlled deep link). The same defect in `buildConflictContext` could cause file content from a sibling directory to be read and forwarded to an external LLM (Copilot) prompt if triggered along a conflict-resolution code path that lets the conflict file list or working directory be influenced by repository content.

### Likelihood Explanation
Exploitation requires: (1) the victim clicks an attacker-crafted `x-github-client://` link, and (2) a same-parent-prefixed sibling directory exists locally. Condition (2) is opportunistic rather than guaranteed, which lowers reliability, but it is a realistic and common naming pattern for developers (forks, `.wiki` folders, renamed re-clones). This keeps likelihood moderate rather than low, since no local/admin access or pre-existing malware is required — only a link click, which matches the accepted threat model.

### Recommendation
Fix the containment check to be separator-aware, e.g.:

```ts
const relative = Path.relative(realRoot, realResolved)
const isWithin =
  relative === '' ||
  (!relative.startsWith('..') && !Path.isAbsolute(relative))
return isWithin ? resolved : null
```

or equivalently ensure a trailing separator is appended to `realRoot` before the `startsWith` comparison (handling the exact-equality case separately). Apply this fix in `_resolveWithin` in `app/src/lib/path.ts` so all three exported variants (`resolveWithin`, `resolveWithinPosix`, `resolveWithinWin32`) inherit the corrected check, and add regression tests for the sibling-prefix case (e.g., root `/a/repo` vs candidate `/a/repo-evil`).

### Proof of Concept
```ts
// app/test/unit/path-test.ts (illustrative addition)
it('incorrectly treats a sibling directory with a shared prefix as "within" the root', async () => {
  const tempDir = await mkdtemp(join(tmpdir(), 'boundary-test-'))
  const root = join(tempDir, 'myrepo')
  const sibling = join(tempDir, 'myrepo-secrets')
  await mkdir(root)
  await mkdir(sibling)
  await writeFile(join(sibling, 'secret.txt'), 'top secret')

  // Attacker-controlled deep-link filepath: '../myrepo-secrets/secret.txt'
  const resolved = await resolveWithin(root, '..', 'myrepo-secrets', 'secret.txt')

  // BUG: this should be null (outside root) but startsWith() lets it through
  assert.notEqual(resolved, null)
})
```
Triggered in the app via a deep link such as:
`x-github-client://openRepo/https://github.com/owner/myrepo?filepath=../myrepo-secrets/secret.txt`
which reaches `Dispatcher.openRepositoryFromUrl` → `resolveWithin(repository.path, filepath)` → `shell.showItemInFolder(resolved)`. [2](#0-1)

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
