### Title
Path-containment check in `_resolveWithin` uses a bare string-prefix comparison without a path-separator boundary, allowing sibling-directory escapes - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin` (used by `resolveWithin`, and transitively by `buildConflictContext` in `app/src/lib/copilot-conflict-context.ts`, `openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts`, and `app-store.ts`) validates that a resolved path is inside a root directory using `realResolved.startsWith(realRoot)` with no check that the next character after the root is a path separator. [1](#0-0) 

### Finding Description
The containment check is:

```ts
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)

return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

`String.prototype.startsWith` performs a pure character comparison, so a directory whose name is a *superstring* of the root's basename will incorrectly pass. For example, root `/Users/victim/repo` and resolved path `/Users/victim/repository-secrets/creds.env` satisfy `startsWith` because `"/Users/victim/repo"` is literally a character-for-character prefix of `"/Users/victim/repository-secrets/creds.env"`, even though `repository-secrets` is a completely different, unrelated directory. The correct check needs to also verify that the character immediately following the root in `realResolved` is a path separator (or that `realResolved === realRoot`).

This function is the security boundary used by `buildConflictContext` to decide whether a conflicted file path reported by the working tree may be safely read and included in the Copilot prompt/context:

```ts
absolutePath = await resolveWithin(workingDirectory, file.path)
...
content = await readFile(absolutePath, 'utf8')
``` [3](#0-2) 

and by `openRepositoryFromUrl` for opening files from clicked deep links: [4](#0-3) 

The existing unit tests (`app/test/unit/path-test.ts`) only cover `..`-traversal and symlink-escape cases, not the prefix-boundary case, so this bug is not caught by current tests. [5](#0-4) 

### Impact Explanation
If exploitable, this would let `resolveWithin` wrongly treat a path in a sibling directory as being "inside" the repository, causing `buildConflictContext` to read file content from outside the selected repository and transmit it to Copilot (disclosure of local files unrelated to the repo). The same flawed primitive is also relied on in `dispatcher.ts` to gate `shell.showItemInFolder` for deep-link-provided paths.

### Likelihood Explanation
The logic flaw itself is real and unconditional — `_resolveWithin` will always misclassify a sibling directory whose name is a superstring of the root directory's basename as being "within" the root, regardless of caller. However, for the specific attack vector cited (a conflicted file path reported by git), exploitation requires two things the remote attacker does not fully control: (1) the ability to make git report a `file.path` value containing `..`-style traversal that escapes the repo root (conflicted file paths from `git status`/merge machinery are normally repository-relative, sanitized paths, not attacker-injectable traversal strings) and (2) the *victim's local filesystem* to happen to contain a sibling directory whose name is a string-superstring of the cloned repo's directory name (e.g. `repo` vs `repository-secrets`), which the attacker cannot predict or control since it depends on the victim's own folder layout. Without a way to control or predict the neighbor directory name, this significantly limits practical, generically-reachable exploitation via the conflict-context path, even though the underlying comparison bug is demonstrably wrong.

### Recommendation
Change the containment check in `_resolveWithin` to require a path-separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
and add a regression test asserting that a sibling directory whose name is a superstring of the root's basename is rejected.

### Proof of Concept
Add to `app/test/unit/path-test.ts`:
```ts
it('rejects sibling directories that are a string-superstring of the root', async () => {
  const tempDir = await mkdtemp(join(tmpdir(), 'path-test-'))
  const root = join(tempDir, 'repo')
  const sibling = join(tempDir, 'repository-secrets')
  await mkdir(root)
  await mkdir(sibling)
  await writeFile(join(sibling, 'creds.env'), 'SECRET=1')

  const escape = join('..', 'repository-secrets', 'creds.env')
  const result = await resolveWithin(root, escape)
  // Currently wrongly resolves because
  // '/tmp/.../repository-secrets/creds.env'.startsWith('/tmp/.../repo')
  assert.equal(result, null) // fails today; result !== null
})
```
This demonstrates that `_resolveWithin`'s `startsWith` check accepts a path in an unrelated sibling directory whenever the relative segment can reach it (e.g. via a `..`-containing path), confirming the containment logic itself is broken independent of how `file.path` is sourced.

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

**File:** app/src/lib/copilot-conflict-context.ts (L391-438)
```typescript
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1971)
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
