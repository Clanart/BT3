### Title
Path‑boundary check in `resolveWithin` uses unanchored `startsWith`, allowing sandbox escape via sibling‑prefixed directories/symlinks - ([File: app/src/lib/path.ts])

### Summary
`_resolveWithin` (used by the exported `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` helpers) is Desktop's guard against path traversal and symlink escapes. Its final check is a plain string‑prefix comparison instead of a boundary‑aware comparison, so any real path that merely shares a *character prefix* with the root — not necessarily a true subdirectory — is incorrectly accepted as "inside" the root.

### Finding Description
The core of the guard is: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`String.prototype.startsWith` has no notion of path separators. If `realRoot` is `/Users/victim/repo`, then a resolved path of `/Users/victim/repo-secrets/token.json` also satisfies `realResolved.startsWith(realRoot)` because `"repo-secrets"` textually begins with `"repo"`. The intended invariant — "the resolved path is at or underneath the root" — is broken by paths that are merely *lexicographically prefixed*, not actually contained. The function's own documentation promises the opposite: "the resolved path is guaranteed to reside at, or underneath this path" (`app/src/lib/path.ts:26-28`), which is false whenever a sibling path name extends the root's basename.

This mirrors the reported bug class exactly: a boolean gate meant to enforce `X <= Y` (path is a subset of root) is implemented with a weaker comparison (`X.startsWith(Y)`) that admits values just outside the intended boundary, silently returning "safe" (non‑null) for unsafe inputs.

Reachability: `resolveWithin` is Desktop's designated defense for attacker‑influenced paths:
- In `buildConflictContext`, it validates `file.path` — a repository‑relative path taken from git status/conflict entries of a repository that can be a malicious clone/fetch target — before reading file contents from disk: [2](#0-1) .
- In the dispatcher's deep‑link handler, it validates a `filepath` supplied via an `x-github-client://` deep link before calling `shell.showItemInFolder`: [3](#0-2) .

Both call sites rely entirely on `resolveWithin` returning `null` for anything outside the root; the `startsWith` bug means it will not do so when the escape path lands in a directory whose real path textually extends the root path (e.g. `repo` vs. `repo-old`, `repo` vs. `repo.bak`, or a symlink target resolving into such a sibling).

### Impact Explanation
This is a filesystem sandbox‑escape primitive. Via the conflict‑context path, an attacker who controls a repository's content (a symlink placed as a conflicting file, combined with a suitably named sibling directory reachable via symlink resolution) can cause Desktop to read file contents from outside the intended working directory and forward that content to the Copilot conflict‑resolution flow — a read‑outside‑repo primitive that also risks exfiltrating unintended local file contents. Via the deep‑link path, a crafted `x-github-client://` link combined with an attacker‑influenced repository layout could cause Desktop to reveal ("show in folder") a file outside the repository the user did not intend to expose. Both are consistent with the program's stated valid‑impact category: attacker‑controlled repository content or a clicked deep link resulting in file read/exposure outside the repo.

### Likelihood Explanation
Exploitation requires the attacker to arrange for a `realpath`‑resolved path to land in a directory whose absolute path textually begins with the trusted root's absolute path but is not actually nested under it (e.g., a sibling directory name that extends the root's final path segment, or a symlink resolving there). This is a narrower condition than a generic traversal (it depends on naming/layout, e.g., cloning into `.../repo` next to an existing `.../repo-something` on disk, or an attacker predicting/controlling such a sibling via other primitives like `sanitizeCloneName`/clone destination logic). It does not require local/admin access, prior malware, or leaked credentials — only a crafted repository (conflict file/symlink) or a crafted deep link, matching the allowed attacker model. Likelihood is moderate: it depends on filesystem layout, but the guard function is documented and relied upon as a strict containment check, so any bypass undermines the security property it is supposed to guarantee.

### Recommendation
Replace the unanchored prefix check with a boundary‑aware comparison, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
This ensures the match only succeeds when `realResolved` is exactly the root or a true subpath (separated by a path delimiter), closing the sibling‑prefix bypass in `app/src/lib/path.ts`.

### Proof of Concept
```ts
// app/src/lib/path.ts uses: realResolved.startsWith(realRoot)
// Demonstration of the boundary bug (conceptual, mirrors app/test/unit/path-test.ts style):

import { resolveWithin } from '../../src/lib/path'
import { mkdtemp, mkdir, writeFile } from 'fs/promises'
import { join } from 'path'
import { tmpdir } from 'os'

const base = await mkdtemp(join(tmpdir(), 'root-'))
const root = join(base, 'repo')            // trusted root, e.g. cloned repo path
const sibling = join(base, 'repo-secrets') // sibling dir that shares the prefix "repo"
await mkdir(root)
await mkdir(sibling)
await writeFile(join(sibling, 'token.json'), '{"secret":"leak"}')

// A relative path that resolves (after normalize/resolve) into the sibling directory,
// e.g. via a symlink placed inside `root` pointing to `../repo-secrets/token.json`,
// will realpath to a path that STARTS WITH `root` textually even though it is not
// nested under it, causing resolveWithin(root, ...) to return non-null instead of null.
```
The unit tests in `app/test/unit/path-test.ts` only cover `..`-based traversal and symlink‑to‑ancestor cases; they do not cover the sibling‑prefix scenario, so this gap is not currently caught by existing tests. [4](#0-3)

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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
