### Title
Insufficient boundary check in `resolveWithin()` allows path-prefix bypass into sibling directories - ([File: app/src/lib/path.ts])

### Summary
`resolveWithin()` (and its `resolveWithinPosix`/`resolveWithinWin32` variants) is the shared containment guard used across Desktop to ensure an attacker/repo-controlled relative path cannot escape a trusted root directory (repository path or working directory) before the app reads, writes, or reveals a file. The final containment check uses a raw string `startsWith()` comparison without verifying a path-separator boundary, so a resolved path in a *sibling* directory whose name happens to share the root directory name as a prefix will incorrectly be treated as "inside" the root — exactly the same class of bug as the Solidity report: a boundary/side check that is computed but never actually verified to be on the correct side of the trusted region.

### Finding Description
The core guard is: [1](#0-0) 

```
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

`resolve()` (Node's `path.resolve`) *does* collapse `..` segments arithmetically, so a relative path such as `../sibling-repo-secrets/file` resolved against root `/Users/victim/Documents/GitHub/repo` becomes `/Users/victim/Documents/GitHub/sibling-repo-secrets/file` — syntactically outside the intended root. The final check is supposed to catch this, but `realResolved.startsWith(realRoot)` is a pure string-prefix comparison: any sibling path whose name starts with the exact characters of the root directory name (e.g. root `.../repo` vs. sibling `.../repo-backup`, `.../repo.wiki`, `.../repo-2`) will pass the check even though it is a completely different directory. The function never confirms that the character immediately following the shared prefix is a path separator (or that the strings are equal), which is the "verify it's on the correct side" step that the Solidity report calls out as missing.

This helper is relied upon as the sole containment guard in multiple places that consume externally influenced paths:
- Deep-link file reveal handler, where `filepath` comes directly from an `x-github-client://` URL the user clicked: [2](#0-1) 
- Copilot conflict-resolution context builder, which resolves repository-relative file paths before reading file content off disk: [3](#0-2) 
- Copilot conflict-resolution writer, which resolves a path before writing model-generated content to disk: [4](#0-3) 

Existing regression tests only cover the intended "traverse out and back in" and symlink-escape cases and do not exercise the sibling-prefix collision, so the gap is untested: [5](#0-4) 

### Impact Explanation
Any caller that trusts `resolveWithin` as a hard containment boundary can be tricked into treating a path in a same-parent sibling directory as "inside the repo" if that sibling's name happens to begin with the repo's directory name (a realistic collision given common naming conventions such as `repo`, `repo.wiki`, `repo-old`, `repo-2`, fork/clone duplicates, etc., which Desktop itself can create side-by-side under the same GitHub Clones folder). Depending on the call site this can lead to:
- Revealing/opening a file outside the intended repository via the `openRepositoryFromUrl` deep-link handler (information disclosure of file existence/location from an attacker-controlled link).
- Reading unrelated on-disk content into the Copilot conflict-resolution prompt context if a crafted conflict-file list can be steered toward a sibling path.
- Writing AI-resolved content to a file outside the repository, corrupting or creating files the user did not intend to touch, if a resolution path can be steered similarly.

The most directly attacker-reachable path is the deep-link `filepath` parameter, which matches the "link/deep link the user clicks" category, and it drives a boundary check that can be defeated without any symlink trickery — just a `..` traversal plus a favorably-named sibling directory.

### Likelihood Explanation
Exploitability depends on the attacker knowing or being able to predict the name of a sibling directory that shares the root's name as a prefix. This is not guaranteed in general, but Desktop's own conventions (cloning the same repo under variant names, wikis, forks) make such collisions plausible in real user environments, and the check gives a false sense of safety since it appears to enforce containment but does not verify the separator boundary. The other two call sites (Copilot context building/writing) are more constrained because the file paths they use are derived from git-reported conflict paths rather than fully attacker-free-form strings, reducing (but not eliminating, depending on how conflict paths are sourced) their independent exploitability.

### Recommendation
Fix the containment check in `app/src/lib/path.ts`'s `_resolveWithin` to require an exact match or a proper separator boundary after the prefix, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test asserting that a sibling directory whose name is prefixed by the root's basename (e.g. root `.../repo`, target `.../repo-evil/secret`) is rejected.

### Proof of Concept
1. Suppose a repository is cloned to `/Users/victim/Documents/GitHub/repo`, and a sibling directory `/Users/victim/Documents/GitHub/repo-secrets` also exists on disk (a plausible naming collision, e.g. from a prior clone, wiki checkout, or backup folder).
2. An attacker sends the victim a deep link: `x-github-client://openRepo/https://github.com/owner/repo?filepath=../repo-secrets/notes.txt`.
3. `openRepositoryFromUrl` receives `filepath = "../repo-secrets/notes.txt"`, which is not absolute, so it passes to `resolveWithin(repository.path, filepath)`.
4. Inside `_resolveWithin`, `resolve()` collapses this to `/Users/victim/Documents/GitHub/repo-secrets/notes.txt`, which `startsWith('/Users/victim/Documents/GitHub/repo')` evaluates `true` (string prefix match), so the guard incorrectly returns the path as "resolved".
5. `shell.showItemInFolder(resolved)` is called on a file outside the intended repository, demonstrating the boundary check's ineffectiveness against sibling-directory collisions.

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

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
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
