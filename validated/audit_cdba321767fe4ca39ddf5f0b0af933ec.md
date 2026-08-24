### Title
Missing separator boundary in `resolveWithin`'s containment check allows sibling-directory path escape - ([File: app/src/lib/path.ts])

### Summary
`_resolveWithin` in `app/src/lib/path.ts` enforces the "resolved path must stay inside root" invariant using a plain string-prefix test, `realResolved.startsWith(realRoot)`, instead of checking for an exact match or a boundary at a path separator. This is the same bug class as the external report: a security-relevant boolean check that looks correct but omits the condition needed to make it actually mean what it claims, silently accepting cases it should reject.

### Finding Description
`resolveWithin` (and its `resolveWithinPosix`/`resolveWithinWin32` wrappers) is documented as guaranteeing the resolved path "is guaranteed to reside at, or underneath" `rootPath`. [1](#0-0) 

The final containment check is:
```ts
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

`String.prototype.startsWith` performs a raw character-prefix comparison with no awareness of path-segment boundaries. If `realRoot` is e.g. `/Users/victim/Documents/GitHub/myrepo` and a caller-supplied relative segment resolves (after traversal) to `/Users/victim/Documents/GitHub/myrepo-evil/secret.txt`, the check passes because the string `"…/myrepo-evil/…"` starts with `"…/myrepo"` — even though `myrepo-evil` is a completely different, sibling directory outside the intended root. The correct check must additionally require `realResolved === realRoot || realResolved.startsWith(realRoot + sep)`.

`resolveWithin` is invoked with attacker-influenced relative path segments in at least two places:
- `app/src/ui/dispatcher/dispatcher.ts`, handling the `filepath` query parameter of the `x-github-client://openRepo/...` deep link. Absolute paths are rejected first, but a relative traversal segment (e.g. `../myrepo-evil/secret.txt`) is passed straight to `resolveWithin(repository.path, filepath)`, and on success the result is opened via `shell.showItemInFolder(resolved)`. [3](#0-2) 
- `app/src/lib/copilot-conflict-context.ts`, resolving repository-relative conflicted-file paths before reading file contents with `readFile(absolutePath, 'utf8')` and feeding them into a Copilot prompt. [4](#0-3) 

The existing unit tests for `resolveWithin` only cover `..`/`../..` full escapes, absolute-but-contained paths, null bytes, and symlink traversal — none of them exercise the sibling-directory prefix case, so this gap is not covered by the test suite. [5](#0-4) 

### Impact Explanation
Where the attacker can influence both the pathSegments and knows (or can predict/enumerate, e.g. via common naming conventions like `<repo>-fork`, `<repo>-old`, `<repo>2`) a sibling directory name that has the repository's directory name as a literal prefix, this check incorrectly treats paths in that sibling directory as "inside" the repository. Depending on the call site, this can be used to make Desktop open (`shell.showItemInFolder`) or read (`copilot-conflict-context.ts`'s `readFile`) files outside the intended repository root — a boundary violation of exactly the kind `resolveWithin` was written to prevent, matching the "read outside the repo" category in the given valid-impact list.

### Likelihood Explanation
Exploitation requires the resolved sibling path to actually exist on disk (readFile/showItemInFolder both operate on existing files), and requires the attacker (via a crafted deep link or crafted PR/merge conflict file list) to guess or know a sibling directory name that is a prefix-superset of the victim's actual repository folder name. This is a real but constrained precondition — it is not a guaranteed-effective attack against an arbitrary victim, but it is a genuine flaw in a supposedly hardened containment primitive that is reused across multiple trust boundaries, and it silently defeats the "prevent escaping the repo root" guarantee whenever the naming coincidence exists.

### Recommendation
Fix the boundary check in `_resolveWithin` to require an exact match or a properly delimited prefix, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test asserting that `resolveWithin('/a/root', '../root-evil/secret')` returns `null` even though the string `"/a/root-evil/secret"` starts with `"/a/root"`.

### Proof of Concept
1. Victim has cloned a repository to `/Users/victim/Documents/GitHub/myrepo` and separately has (or an attacker predicts) a sibling directory `/Users/victim/Documents/GitHub/myrepo-evil/secret.txt`.
2. Attacker sends the victim a deep link: `x-github-client://openRepo/https://github.com/owner/myrepo?filepath=..%2Fmyrepo-evil%2Fsecret.txt`.
3. `openRepositoryFromUrl` rejects the filepath only if `isAbsolute(filepath)` is true; `../myrepo-evil/secret.txt` is relative, so it proceeds to `resolveWithin(repository.path, filepath)`. [3](#0-2) 
4. Inside `_resolveWithin`, `resolve(normalizedRoot, normalizedRelative)` produces `/Users/victim/Documents/GitHub/myrepo-evil/secret.txt`, and `realResolved.startsWith(realRoot)` evaluates to `true` since `realRoot = "/Users/victim/Documents/GitHub/myrepo"` is a literal string prefix of `realResolved`. [6](#0-5) 
5. Desktop calls `shell.showItemInFolder(resolved)` on the out-of-repo file, revealing/opening content outside the repository the user believed they were interacting with.

Note: I was unable to fully verify the exact runtime behavior of `Path.resolve`/`Path.normalize` on all traversal-segment edge cases without executing code, and did not have access to run the test suite to confirm this exact PoC end-to-end — this analysis is based on static reading of the cited source and existing unit tests.

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
