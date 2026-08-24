### Title
`resolveWithin` path-containment check uses unanchored `startsWith`, allowing sibling-directory escape - (File: app/src/lib/path.ts)

### Summary
The Y2K-Finance report describes a broken invariant: two logically-linked boundary checks (`<` vs `>`) disagree at the boundary, letting an attacker slip a deposit past a check that was supposed to close the window at exactly `epochBegin`. The general bug class is "a containment/boundary check that looks correct but has a gap at the edge condition, and something attacker-influenced lands exactly in that gap." The closest real analog in this codebase is the repo/file "is this path inside the root" check in `resolveWithin`, which is the single choke point Desktop uses to decide whether an attacker-influenced relative path (from a deep link or from file paths sourced out of the working tree) is allowed to be used.

### Finding Description
`_resolveWithin` in `app/src/lib/path.ts` is meant to guarantee that a resolved path can never fall outside a given root directory: [1](#0-0) 

The containment decision is made purely with a string prefix comparison:
```
return realResolved.startsWith(realRoot) ? resolved : null
```
This is missing a path-separator boundary check. If `realRoot` is e.g. `/Users/victim/Documents/GitHub/project` and there exists a sibling directory `/Users/victim/Documents/GitHub/project-secrets`, then `realResolved = /Users/victim/Documents/GitHub/project-secrets/secret.txt` satisfies `realResolved.startsWith(realRoot)` even though it is a completely different directory tree. The function will return this path as "resolved" (i.e., "inside the root"), just as `epochHasNotStarted`'s `>` comparator incorrectly let a deposit through at the exact boundary the sibling-invariant was supposed to close.

The existing unit tests in `app/test/unit/path-test.ts` do not exercise this boundary at all — they test `..`, `../..`, symlink escapes, and null bytes, but never a same-prefix sibling directory, so this gap has no regression coverage: [2](#0-1) 

`resolveWithin` is the security boundary used from attacker-reachable code paths:
- `dispatcher.ts`'s `openRepositoryFromUrl`, which is invoked for the `x-github-client://openRepo?url=...&filepath=...` deep link — a link an attacker can get a victim to click. The `filepath` (attacker-controlled) is passed straight into `resolveWithin(repository.path, filepath)` and, if it resolves, is passed to `shell.showItemInFolder`: [3](#0-2) 

- `copilot-conflict-context.ts`'s `buildConflictContext`, which resolves file paths taken from merge-conflict metadata (attacker-influenced via a crafted repository/branch being merged) against the repository working directory before reading file contents: [4](#0-3) 

Neither call site adds its own separator check; both rely entirely on `resolveWithin`'s `startsWith` guarantee.

### Impact Explanation
If a victim has two directories on disk that share a path prefix (a very common developer pattern, e.g. `project` and `project-secrets`, `api` and `api-internal`, `app` and `app-config`), an attacker who controls the `filepath` query parameter of an `x-github-client://openRepo` deep link, or who crafts conflict content resolved through `buildConflictContext`, can cause Desktop to treat a path in the sibling directory as "inside the repository." This can lead to disclosure of file locations/content outside the intended repository boundary (via `shell.showItemInFolder` revealing sensitive paths in Finder/Explorer, or reading unintended file contents into the Copilot-conflict-resolution flow and potentially onward to a remote LLM). This matches the requested valid-impact class: "a link or deep link the user clicks... resulting in... file... read outside the repo."

### Likelihood Explanation
Exploitation requires: (1) the victim to have a directory structure where an attacker-guessable/observable sibling directory shares a path prefix with an open repository, and (2) the victim to click a crafted deep link (for the dispatcher path) or to merge/pull a branch containing crafted conflict metadata (for the Copilot path). This is a real, unprivileged, no-local-access precondition consistent with the “Valid Impact” criteria, though it depends on directory-naming coincidence, which lowers likelihood relative to a universally exploitable bug. It does not require admin rights, pre-existing malware, or leaked credentials.

### Recommendation
Change the containment check in `_resolveWithin` (`app/src/lib/path.ts`, line 71) to require a path-separator (or exact equality) boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests covering same-prefix sibling directories (e.g., root `/tmp/foo` vs. candidate `/tmp/foo-evil/secret`) for both POSIX and Windows separators.

### Proof of Concept
1. Victim has two sibling directories: `/Users/victim/Documents/GitHub/project` (an open Desktop repository) and `/Users/victim/Documents/GitHub/project-secrets` (unrelated, sensitive files).
2. Attacker sends the victim a link: `x-github-client://openRepo?url=<project-repo-url>&filepath=..%2Fproject-secrets%2Fsecret.txt`.
3. `parseAppURL` extracts `filepath = "../project-secrets/secret.txt"` and passes it to `openRepositoryFromUrl` → `resolveWithin(repository.path, filepath)`.
4. Inside `_resolveWithin`, `resolved` normalizes to `/Users/victim/Documents/GitHub/project-secrets/secret.txt`; `realResolved.startsWith(realRoot)` evaluates true because `"...project-secrets/secret.txt".startsWith("...project")` is true as a raw string comparison.
5. `resolveWithin` returns the sibling-directory path as "inside the repo," and `shell.showItemInFolder(resolved)` reveals the sensitive file/location to the user via a deep link they clicked, without any Desktop repository actually containing that file.

Note: this PoC demonstrates the missing-boundary condition in the containment primitive itself (confirmed by direct code reading); I was not able to execute the deep-link flow end-to-end in this environment, so the exact behavior of `shell.showItemInFolder` on a non-existent-in-repo-but-real filesystem path should be validated in a live Desktop build.

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
