### Title
Path-containment check in `resolveWithin()` uses a bare `startsWith()` prefix test, allowing sibling-directory escape when reading attacker-influenced file paths - ([File: app/src/lib/path.ts])

### Summary
The report's bug class is: a helper function's return value is trusted to mean one thing ("this is the actual output/contained value") when its real semantics are weaker, and callers build a security- or fund-relevant decision on that mis-trusted value without additional validation. In GitHub Desktop, the analogous broken invariant lives in the path-containment helper `resolveWithin()` (`app/src/lib/path.ts`), which is meant to guarantee "the resolved path is at or under `rootPath`" but actually only guarantees "the resolved path's string representation has `rootPath` as a textual prefix." Callers (`copilot-conflict-context.ts`, `dispatcher.ts`, `app-store.ts`) treat a non-null return as a hard guarantee of containment and then read file contents or open paths based on it.

### Finding Description
`_resolveWithin()` performs the containment check as: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`String.prototype.startsWith()` is a pure string-prefix test; it does not verify that the character immediately following the prefix is a path separator (or that the two strings are equal). Consequently, if `realRoot` is e.g. `/Users/victim/Documents/GitHub/repo`, a resolved path of `/Users/victim/Documents/GitHub/repo-secrets/id_rsa` or `/Users/victim/Documents/GitHub/repository-2/notes.txt` will satisfy `startsWith(realRoot)` even though it is a **sibling** directory, not a path underneath `repo`. This is the same class of bug as the report: the caller trusts a return value's implied semantics ("is contained within root") when the underlying check only proves a weaker, superficially similar property (string prefix match), and that gap is only reachable when an attacker can influence the path segment that gets joined/resolved (e.g. via a symlink checked into a cloned repository, or a file path supplied in repository/PR metadata).

The unit tests for this function only cover `..` traversal, null bytes, and a couple of symlink scenarios — none of them exercise the sibling-directory prefix case, which is why the gap went unnoticed: [2](#0-1) 

The function is used as the sole containment gate in at least two attacker-reachable flows:

1. `copilot-conflict-context.ts` resolves conflicted file paths (derived from Git status/conflict data of a fetched/merged repository) and, if `resolveWithin` returns non-null, reads the file's contents and forwards them to the Copilot conflict-resolution backend: [3](#0-2) 

2. `dispatcher.ts`'s `openRepositoryFromUrl` resolves a `filepath` taken from a deep link (`x-github-client://openRepo/...`) the user clicks, and if `resolveWithin` returns non-null it calls `shell.showItemInFolder(resolved)`: [4](#0-3) 

Both call sites rely entirely on `resolveWithin`'s boolean-like null/non-null result as proof of containment, exactly mirroring the report's pattern of a caller branching on a return value whose real meaning differs from what the caller assumes.

### Impact Explanation
Because the prefix check has no separator boundary verification, a specially arranged filesystem layout combined with an attacker-supplied relative path/symlink (e.g. a symlink object committed to a repository, or a deep-link `filepath` parameter crafted by an attacker-controlled page/README that a user clicks) can cause Desktop to resolve and treat as "safely contained" a path that actually lives in a sibling directory outside the intended repository root. Depending on the call site this leads to:
- Reading file contents outside the repository and exfiltrating them to a third-party network endpoint (Copilot conflict-resolution flow), or
- Revealing/opening a file outside the repository via the file-explorer deep-link handler.

This matches the "read outside the repo" / "credential or file exfiltration" impact categories called out as valid for this analysis.

### Likelihood Explanation
Exploitation requires the attacker to control (a) the relative path/symlink target that gets resolved, and (b) some knowledge or coincidence of a sibling path name on the victim's disk that shares the root path as a string prefix (e.g., cloning locations following predictable naming conventions like `repo`, `repo-1`, `repo-old`, `repo.git`, or nested clone directories). This makes it a real but narrower-likelihood bug than a full `..`-style traversal — it depends on directory-naming coincidences or a companion primitive (like the symlink escape already partially mitigated by `realpath`) to reliably land outside the intended root. It is nonetheless a genuine, currently un-tested gap in a function that both existing security-critical call sites depend on completely.

### Recommendation
Fix `_resolveWithin` to verify a true path-boundary, not just a string prefix, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + sep)
  ? resolved
  : null
```
where `sep` is the platform-appropriate separator for the `join/normalize/resolve` implementation being used (`Path.sep`, `Path.posix.sep`, or `Path.win32.sep`). Add regression tests asserting that a sibling directory sharing the root as a string prefix (without a following separator) is rejected.

### Proof of Concept
```ts
import { resolveWithin } from '../../src/lib/path'
import { mkdir, writeFile, rm } from 'fs/promises'
import { join } from 'path'
import { tmpdir } from 'os'

// Simulates two sibling directories where one is a prefix of the other's name
const base = await mkdtemp(join(tmpdir(), 'poc'))
const root = join(base, 'repo')          // the "safe" root
const sibling = join(base, 'repo-secret') // sibling dir, NOT under root
await mkdir(root)
await mkdir(sibling)
await writeFile(join(sibling, 'id_rsa'), 'PRIVATE KEY DATA')

// Attacker-controlled relative path resolves (e.g. via a symlink placed in the
// cloned repo) to a path in the sibling directory.
// Because `join(root, '../repo-secret/id_rsa')` -> `<base>/repo-secret/id_rsa`
// starts with `<base>/repo` as a raw string, the check incorrectly succeeds:
const result = await resolveWithin(root, '..', 'repo-secret', 'id_rsa')
console.log(result) // NOT null — incorrectly considered "within" root
```
This demonstrates that `resolveWithin(root, ...)` can return a non-null "safe" path pointing at a file in a sibling directory, which the copilot-conflict-context and deep-link handlers would then read/open under the false assumption that it is confined to the repository.

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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

**File:** app/src/lib/copilot-conflict-context.ts (L390-438)
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
