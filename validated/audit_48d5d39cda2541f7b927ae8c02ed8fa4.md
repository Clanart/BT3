### Title
Path-boundary check in `resolveWithin()` uses unbounded `startsWith`, allowing conflict-file reads (and deep-link file reveals) to escape the repository root - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin()` is the shared guard GitHub Desktop uses to make sure a repository‑relative path can't escape the repository root before it's read from disk or shown to the user. The final containment check compares the resolved real path to the real root using plain string `startsWith`, with no trailing path‑separator boundary. A sibling directory whose name happens to start with the root directory's name (e.g. root `…/GitHub/repo` vs. sibling `…/GitHub/repo-secrets`) will pass the check even though it is not actually nested under the root. This mirrors the oracle report's core flaw: a boundary/tolerance test that looks strict but is quietly wider than intended, letting untrusted input be treated as "still inside the trusted window" when it is not.

### Finding Description
The guard is defined here: [1](#0-0) 

```
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

`realResolved.startsWith(realRoot)` treats any path whose *string* begins with `realRoot` as being "within" the root — it never checks for a following path separator (or exact equality). If a symlink inside the repository resolves to a real path such as `/Users/victim/Documents/GitHub/repo-secrets/data`, and the repository root is `/Users/victim/Documents/GitHub/repo`, the check passes even though `repo-secrets` is a completely separate sibling directory, not a subdirectory of `repo`.

The existing unit tests only cover traversal-then-back-in and symlink-escape-fully-outside scenarios [2](#0-1) ; none of them cover the "sibling directory sharing a name prefix" case, so this gap is not caught.

`resolveWithin()` is relied on by at least two consumers that process attacker-influenced, repository-scoped paths:

1. **Deep-link file reveal** — the `open-repository-from-url` protocol handler accepts a `filepath` parameter parsed straight from a URL an attacker can construct and get a user to click (`x-github-client://openrepo/...` / `github-mac://openrepo/...`), and calls `resolveWithin` before revealing the file in Explorer/Finder: [3](#0-2) [4](#0-3) 

2. **Copilot merge-conflict context builder** — for each conflicted file, `buildConflictContext()` resolves the file path against the repository's working directory before reading its contents and forwarding them to the Copilot backend as `rawContent`: [5](#0-4) 

In both cases the intended invariant is "never touch anything outside the repository root." Because `resolveWithin` can be fooled by a same‑prefix sibling directory reached through a symlink, that invariant is not actually enforced.

### Impact Explanation
For the conflict-context path: an attacker who controls a cloned/fetched repository (a very common trust boundary in Desktop — cloning any third‑party repo, or merging a branch/PR from an untrusted fork) can commit a symlink whose target, once resolved with `realpath`, lands in a sibling directory that shares the repository directory's name as a prefix (attacker can often choose or predict this, e.g. by naming the clone folder to make a plausible sibling exist, or by targeting well-known sibling layouts in a monorepo/workspace directory). When that symlinked file participates in a real merge/rebase/cherry-pick conflict, `buildConflictContext()` will pass the sibling-escaping path through `resolveWithin`, read its contents from disk, and include them as `rawContent` sent to the Copilot conflict-resolution backend — i.e., disclosure of file contents outside the repository to an external service, without the user consenting to share that file.

For the deep-link path: an attacker-crafted `x-github-client://openrepo/...?filepath=...` link, combined with a symlink already present in the target/attacker repository, can cause Desktop to call `shell.showItemInFolder()` on a path outside the repository, revealing files/directories the user did not intend to expose via a single unprompted click on a link.

Both cases match the required impact class: attacker controls a cloned/fetched repository or a link the user clicks, and the result is file read outside the repo boundary (with potential exfiltration to a network service in the Copilot case).

### Likelihood Explanation
**Medium.** Exploitation requires: (a) a git-tracked symlink inside the attacker's repository whose target real-path happens to be a same-prefix sibling of the repo's directory name, and (b) that symlinked path actually appearing in a real merge/rebase/cherry-pick conflict, or being referenced via a crafted deep link. Constructing (a) is straightforward for an attacker preparing a malicious repository; getting (b) to trigger naturally requires either a targeted social-engineering merge scenario or the deep-link vector, which needs no local access or credentials — only a link click. This is a real, but not trivially deterministic, precondition versus the oracle bug's "any short-heartbeat feed during normal volatility" trigger, hence Medium rather than High likelihood.

### Recommendation
Fix `_resolveWithin` to require exact match or a following separator, not just a string prefix:

```ts
return realResolved === realRoot ||
  realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```

Add a regression test asserting that a sibling directory sharing a name prefix with the root (e.g. root `.../repo`, target `.../repo-evil/file`) is correctly rejected, complementing the existing symlink-escape tests in `app/test/unit/path-test.ts`.

### Proof of Concept
Conceptual reproduction (would need to be added as a Node test alongside `app/test/unit/path-test.ts`):

```ts
it('fails for a sibling directory that shares a name prefix with the root', async () => {
  const parent = await mkdtemp(join(tmpdir(), 'resolve-within-'))
  const root = join(parent, 'repo')
  const sibling = join(parent, 'repo-secrets')
  await mkdir(root)
  await mkdir(sibling)
  await writeFile(join(sibling, 'secret.txt'), 'top secret')

  // Symlink inside "repo" that resolves to the sibling directory.
  const symlinkPath = join(root, 'escape')
  await symlink(sibling, symlinkPath)

  // Should be null (outside root) but currently resolves successfully
  // because "<parent>/repo-secrets/...".startsWith("<parent>/repo") is true.
  const result = await resolveWithin(root, 'escape', 'secret.txt')
  assert.equal(result, null) // FAILS with current implementation
})
``` [6](#0-5)

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

**File:** app/test/unit/path-test.ts (L65-100)
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

**File:** app/src/lib/parse-app-url.ts (L98-125)
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
  }
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-431)
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
```
