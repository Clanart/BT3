## Finding: Path-containment check in `resolveWithin` uses unanchored `startsWith`, allowing escape into sibling directories that share a name prefix

### Title
Path traversal into sibling repository via unanchored `startsWith` boundary check in `resolveWithin()` — (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin()` is Desktop's central guard used to keep externally‑supplied relative paths confined to a repository's working directory. Its final containment check is `realResolved.startsWith(realRoot)` [1](#0-0) . Like the reported `startTime` bug, the check verifies only one side of the required invariant (that the resolved path begins with the same *characters* as the root) instead of the actually needed invariant (that the resolved path is the root or a path *component* underneath it). A resolved path in a sibling directory whose name is a superstring of the root directory's name (e.g. `webapp-1` vs `webapp`) incorrectly passes the check.

### Finding Description
`_resolveWithin` normalizes and resolves the caller-supplied `pathSegments` against `rootPath`, then confirms containment purely with a string prefix comparison, with no path-separator boundary check:
```ts
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

If `realRoot` is `/Users/victim/Documents/GitHub/webapp` and an attacker crafts a relative path that resolves to `/Users/victim/Documents/GitHub/webapp-1/secret.txt`, the check `'/Users/victim/Documents/GitHub/webapp-1/secret.txt'.startsWith('/Users/victim/Documents/GitHub/webapp')` evaluates to `true`, even though `webapp-1` is a completely different, sibling repository. The existing test suite only covers `..`-traversal and symlink-traversal cases [3](#0-2) ; it never exercises the prefix-collision case, so this gap is unguarded.

This function is reachable from attacker-influenced input in two places:

1. **Deep link `openrepo` action** — `filepath` is a query-string value taken directly from an `x-github-client://openrepo?...&filepath=...` URL (the exact "Open in Desktop" deep-link flow), only checked for being non-absolute, then passed straight to `resolveWithin`:
```ts
if (isAbsolute(filepath)) { ... return }
const resolved = await resolveWithin(repository.path, filepath)
if (resolved !== null) {
  shell.showItemInFolder(resolved)
}
``` [4](#0-3) 

2. **Copilot conflict-resolution file writes** — `resolution.path` (per-file paths tied to stored conflict resolutions) is resolved with the same helper before content is written to disk:
```ts
const absolutePath = await resolveWithin(repository.path, resolution.path)
...
await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
``` [5](#0-4) 

The sibling-name precondition is realistic in Desktop specifically because Desktop's own default clone naming picks a numeric-suffixed directory (e.g. `webapp-1`, `webapp-2`) when a directory with the target repository name already exists — a very common situation for users who have cloned a repo and one of its forks, or multiple repos with the same base name. This naturally produces exactly the `webapp` / `webapp-1` sibling layout that defeats the `startsWith` check. `clone-path-safety-test.ts` demonstrates Desktop's awareness of clone-path containment risks in general (it validates `sanitizeCloneName` against `..`-based escapes) [6](#0-5) , but does not cover this unrelated prefix-boundary flaw in `resolveWithin`.

### Impact Explanation
- Via the deep-link `filepath` parameter, an attacker who gets a victim to click a crafted `x-github-client://openrepo?...` link can cause Desktop to reveal the location of (`shell.showItemInFolder`) or open a file that lives inside a *different*, unrelated local repository on disk, outside of the repository the link nominally targets — a confinement/read-scope violation.
- Via the Copilot conflict-resolution path, the same broken boundary check means `writeFile` can silently write resolved content into a file inside a sibling repository (`webapp-1`) instead of the one actually undergoing conflict resolution (`webapp`), corrupting a repository the user did not intend to touch and did not review — matching the report's "silent corruption of what the user commits" impact class.

### Likelihood Explanation
Exploitation only requires: (1) the victim has two locally-cloned repositories whose folder names share a prefix (a routine outcome of Desktop's own default clone-naming scheme for repos/forks with identical names), and (2) the victim clicks an attacker-supplied deep link or triggers the Copilot conflict-resolution flow with a crafted path. No local/physical access, admin rights, or pre-existing malware is required — only the ordinary "attacker-controlled link" and "attacker-controlled repo content" primitives explicitly in scope.

### Recommendation
Fix the boundary check in `_resolveWithin` to require the path separator (or exact equality) rather than a raw string prefix, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
(using the platform-appropriate separator for the `options` passed in, i.e. `/` for `Path.posix` and `\\` for `Path.win32`). Add a regression test with sibling directories that share a name prefix (e.g. `webapp` vs `webapp-1`) to `path-test.ts`.

### Proof of Concept
```ts
import { resolveWithin } from '../../src/lib/path'
import { mkdir, writeFile } from 'fs/promises'
import { join } from 'path'

// Setup: two sibling repos with a shared name prefix, mirroring Desktop's
// own clone-naming behavior when "webapp" already exists locally.
await mkdir('/tmp/GitHub/webapp', { recursive: true })
await mkdir('/tmp/GitHub/webapp-1', { recursive: true })
await writeFile('/tmp/GitHub/webapp-1/secret.txt', 'private contents')

// Simulate dispatcher.openRepositoryFromUrl's containment check for
// repository.path = '/tmp/GitHub/webapp' with an attacker-supplied filepath
// from a deep link that traverses to the sibling repo and back in:
const resolved = await resolveWithin(
  '/tmp/GitHub/webapp',
  '../webapp-1/secret.txt'
)

// BUG: resolved !== null — the sibling repo's file is treated as "within"
// /tmp/GitHub/webapp because '/tmp/GitHub/webapp-1/...' starts with the
// string '/tmp/GitHub/webapp'.
console.log(resolved) // '/tmp/GitHub/webapp-1/secret.txt'
```

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

**File:** app/test/unit/clone-path-safety-test.ts (L43-71)
```typescript
  it('traversal payload clone path stays contained (POSIX)', () => {
    const result = parseRepositoryIdentifier(
      'https://evil.com/owner/x..\\..\\..\\.\\.ssh.git'
    )
    assert(result !== null)
    const safeName = sanitizeCloneName(result.name)
    assert(safeName !== null)
    const baseDir = '/Users/victim/Documents/GitHub'
    const resolved = Path.resolve(Path.join(baseDir, safeName))
    assert(
      resolved.startsWith(Path.resolve(baseDir)),
      `Clone path "${resolved}" escapes base dir`
    )
  })

  it('traversal payload clone path stays contained (Windows)', () => {
    const result = parseRepositoryIdentifier(
      'https://evil.com/owner/x..\\..\\..\\.\\.ssh.git'
    )
    assert(result !== null)
    const safeName = sanitizeCloneName(result.name)
    assert(safeName !== null)
    const baseDir = 'C:\\Users\\victim\\Documents\\GitHub'
    const resolved = Path.win32.resolve(Path.win32.join(baseDir, safeName))
    assert(
      resolved.startsWith(Path.win32.resolve(baseDir)),
      `Clone path "${resolved}" escapes base dir on Windows`
    )
  })
```
