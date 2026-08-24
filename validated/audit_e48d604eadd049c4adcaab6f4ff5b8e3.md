## Title
Boundary-check bypass in `resolveWithin()` path-containment guard via prefix matching without separator - (File: app/src/lib/path.ts)

### Summary
The zkasm bug is a classic "boundary comparison excludes the exact edge value" flaw: a `<` check that should have been `<=` (or vice versa) lets an attacker land exactly on the boundary and read out-of-bounds memory. GitHub Desktop's repo-containment guard, `resolveWithin()`, has the analogous flaw in string-space instead of index-space: it uses `String.prototype.startsWith()` to decide whether a resolved path is "inside" the root directory, without verifying that a path separator immediately follows the root prefix. A sibling directory whose name has the root directory's name as a prefix (e.g. `repo` vs `repo-evil`) satisfies `startsWith` even though it is a completely different, sibling directory — an off-by-one at the directory-boundary level.

### Finding Description
`resolveWithin()` is Desktop's single chokepoint for confirming that a repository-relative path, once resolved (and `realpath`'d to defeat symlinks), stays inside the repository root: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`startsWith` is a pure string-prefix test. It does **not** check that `realRoot` is followed by `path.sep` (or that `realResolved === realRoot`). Consequently, if the repository lives at `/Users/victim/Documents/GitHub/repo` and there also exists (or an attacker can arrange for there to exist) a sibling path `/Users/victim/Documents/GitHub/repo-secrets`, then:

- `resolveWithin('/Users/victim/Documents/GitHub/repo', '../repo-secrets/id_rsa')`
- resolves to `/Users/victim/Documents/GitHub/repo-secrets/id_rsa`
- `realResolved.startsWith(realRoot)` → `'/Users/.../repo-secrets/id_rsa'.startsWith('/Users/.../repo')` → **true**

The function returns the path as "safe" even though it is a completely different, sibling directory outside the repository root — exactly the bug-class described in the report: the guard fails to reject a value sitting on/just past the intended boundary because the comparison isn't strict enough.

This differs from the well-tested traversal cases in the existing unit tests (`app/test/unit/path-test.ts`), which only check `..`-segment traversal and symlink traversal — none test the "sibling-directory-name-is-a-prefix" case, so this gap is unguarded.

### Impact Explanation
`resolveWithin()` is relied upon as the sole safety check in multiple attacker-reachable code paths:

- Deep-link file opening: a crafted `x-github-client://openRepo/<repo-url>?filepath=...` link (a link the user clicks) is parsed by `parseAppURL` and its `filepath` is passed straight into `resolveWithin(repository.path, filepath)` before calling `shell.showItemInFolder(resolved)`: [2](#0-1) 
- Copilot merge-conflict resolution writes model-suggested file content to disk using the same guard, then stages it with `git add`: [3](#0-2) 
- Conflict-context building reads arbitrary conflicted-file content through the same guard: [4](#0-3) 

Because the containment check can be defeated whenever a sibling folder name shares the repo folder's name as a prefix, an attacker who can influence a relative path fed into any of these call sites (via a malicious deep link, or via content that steers the AI-assisted resolution's reported file path) could cause Desktop to read from, reveal, or **silently write to a file outside the intended repository** — directly matching the "silent corruption of what the user commits/pushes" and "file write/read outside the repo" impact categories.

### Likelihood Explanation
Exploitation requires a coincidental (or attacker-arrangeable, e.g. via a previous "Clone" naming pattern like `myapp` / `myapp-secrets` common in real filesystems) sibling directory whose name is prefixed by the repository directory's name, which constrains but does not eliminate real-world likelihood — Desktop users very commonly keep multiple related repos or backup/secret folders side-by-side under the same parent (e.g. `GitHub/api`, `GitHub/api-keys`, `GitHub/api-internal`). The deep-link path is triggerable purely by getting a user to click a link, with no other privilege required, and the write-path via Copilot conflict resolution is reachable without any explicit user path input at all (only requires steering the model's returned `path` field to a sibling-directory guess), making the bug realistically triggerable rather than purely theoretical.

### Recommendation
Fix the boundary check to require an exact match or a path-separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test with a sibling directory whose name is a superstring of the root directory's name (e.g. `root` vs `root-evil`) to `app/test/unit/path-test.ts`.

### Proof of Concept
```ts
import { resolveWithin } from '../../src/lib/path'
import { mkdir, writeFile } from 'fs/promises'
import { join } from 'path'
import { tmpdir } from 'os'

// setup: two sibling dirs, one a prefix of the other
const base = await mkdtemp(join(tmpdir(), 'poc'))
const repoRoot = join(base, 'repo')
const evilSibling = join(base, 'repo-evil')
await mkdir(repoRoot)
await mkdir(evilSibling)
await writeFile(join(evilSibling, 'secret.txt'), 'top secret')

// attacker-controlled relative path (e.g. from a deep-link `filepath` query param)
const result = await resolveWithin(repoRoot, '../repo-evil/secret.txt')

console.log(result)
// Expected (secure): null
// Actual (vulnerable): '<base>/repo-evil/secret.txt'  -- escapes repoRoot but passes the check
``` [5](#0-4)

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

**File:** app/src/lib/stores/app-store.ts (L7233-7259)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }

      // If the user resolved this file externally (e.g. in their editor) while
      // the result dialog was open, git status will report it with no remaining
      // conflict markers. Overwriting it with Copilot's stored content would
      // silently clobber their work, so skip it and let their resolution stand.
      // This mirrors how the manual conflicts dialog determines a file is
      // resolved (`hasUnresolvedConflicts`).
      const onDiskFile = state.changesState.workingDirectory.files.find(
        f => f.path === resolution.path
      )
      if (
        onDiskFile !== undefined &&
        isConflictedFileStatus(onDiskFile.status) &&
        !hasUnresolvedConflicts(onDiskFile.status)
      ) {
        continue
      }

      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
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
