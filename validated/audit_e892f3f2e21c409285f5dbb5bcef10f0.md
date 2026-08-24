## Analysis

The NFT report's core flaw is a **containment/ownership check that exists but is structurally insufficient** — `remove_nft_deposit` performs a lookup keyed by identifier without confirming the identifier actually belongs to the claimed scope (`pool_id`), letting an attacker swap in the wrong resource while the check appears to pass.

The GitHub Desktop analog is the repository-boundary check `resolveWithin`, which is the app's central guard against operations that read/write/open files outside a repository. Its containment test is a **prefix string comparison without a path-separator boundary**: [1](#0-0) 

### Title
Path-boundary bypass in `resolveWithin` via sibling-directory prefix match, exploitable through the `x-github-client://openRepo` deep link - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin` (and its `posix`/`win32` variants) is the single primitive `desktop` relies on to guarantee "resolved path stays inside repository root." Its containment check is:
```ts
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 
This is a bare string-prefix test with no trailing separator/exact-boundary check. A resolved path in a *sibling* directory whose name happens to begin with the root directory's full path string (e.g. root `/Users/victim/Documents/GitHub/project` vs. sibling `/Users/victim/Documents/GitHub/project-secrets`) satisfies `startsWith` even though it is not "at or underneath" the root — exactly the class of bug the NFT report describes: a scope/ownership check that is present but doesn't actually enforce the boundary it claims to.

### Finding Description
`resolveWithin` is documented and tested as guaranteeing the returned path "resides at, or underneath" `rootPath` [3](#0-2) , and the existing test suite only validates `..`-traversal, null bytes, and symlink escapes [4](#0-3) . None of the tests exercise the sibling-directory-name-prefix case, so the missing separator check is unguarded.

This primitive is used by `openRepositoryFromUrl`, the handler for the `x-github-client://openRepo/...` deep link that a user can click from a browser or PR comment. It takes an attacker-supplied `filepath` query parameter, resolves it against the local repository path, and if `resolveWithin` returns non-null, reveals that resolved file in the OS file manager: [5](#0-4) 

The `filepath` parameter comes straight from `parseAppURL`'s `IOpenRepositoryFromURLAction`, with essentially no path-shape validation beyond `isAbsolute` rejection [6](#0-5) [7](#0-6) . An attacker who controls the link (and who can predict or guess that the victim keeps sibling directories under a common parent, e.g. `~/Documents/GitHub/<repo>` and `~/Documents/GitHub/<repo>-secrets` or `<repo>-backup`) can craft `filepath=..%2F<repo>-secrets%2Fcredentials.txt` so that the resolved absolute path escapes the intended repository while still passing the broken `startsWith` check.

The same primitive also gates the write path used for Copilot's AI-driven merge-conflict resolution, where resolved content is written to disk via `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` after a `resolveWithin` check [8](#0-7) , and by the conflict-context builder that reads file contents for the AI prompt [9](#0-8) . I was not able to fully verify, within the available iterations, whether `resolution.path` (the model's structured output) is strictly constrained to the known list of conflicted files before reaching `resolveWithin`/`writeFile` — if it is not tightly validated, the same boundary bug would allow a write outside the repository driven by content from an attacker-controlled cloned/fetched repository (prompt-injection via conflict text), which would be a materially higher-severity variant than the deep-link case. This should be verified directly against `app/src/lib/copilot-conflict-resolution.ts` and the code that produces `copilotResolutions` in `app-store.ts`.

### Impact Explanation
For the deep-link path, the concrete impact is disclosure of the existence/location of files outside the intended repository directory (via `shell.showItemInFolder`), which can leak file/folder names on the victim's disk that the attacker guessed. If the same broken boundary check is reachable from the Copilot write path with insufficiently constrained `resolution.path`, the impact escalates to **arbitrary file write outside the repository**, matching the "silent corruption/write outside the repo" impact category directly.

### Likelihood Explanation
The deep-link vector requires only that the victim click a crafted `x-github-client://openRepo/...` link — a normal, unprivileged, no-local-access precondition explicitly allowed by the task's valid-impact criteria. The attacker also needs the victim to have a sibling directory whose name is a superstring of the repo's full path, which is a real but not universal precondition (common when users organize projects under one parent folder, e.g. `Documents/GitHub/`). This reduces likelihood somewhat but does not eliminate it, since directory layouts like `repo` / `repo-old`, `repo` / `repo2`, or `repo` / `repo.bak` are common.

### Recommendation
Fix `_resolveWithin` in `app/src/lib/path.ts` to require an exact match or a boundary separator, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test with a sibling directory named `<root>-suffix` to lock in the fix, and audit all call sites (`app-store.ts`, `dispatcher.ts`, `copilot-conflict-context.ts`) that treat a non-null `resolveWithin` result as proof of containment.

### Proof of Concept
1. Victim has repositories cloned at `~/Documents/GitHub/project` and also has an unrelated folder `~/Documents/GitHub/project-secrets/notes.txt`.
2. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/owner/project?filepath=..%2Fproject-secrets%2Fnotes.txt`.
3. Victim clicks the link; Desktop opens/selects the existing `project` repository, then calls `resolveWithin(repository.path, "../project-secrets/notes.txt")`.
4. `resolved` becomes `~/Documents/GitHub/project-secrets/notes.txt`; `realResolved.startsWith(realRoot)` is `true` because `".../project-secrets"` starts with the string `".../project"`, even though it is a sibling, not a subdirectory.
5. `shell.showItemInFolder(resolved)` reveals the out-of-repo file to the attacker-crafted link's target, confirming the boundary bypass [10](#0-9) .

### Citations

**File:** app/src/lib/path.ts (L13-24)
```typescript
/**
 * Resolve one or more path sequences into an absolute path underneath
 * or at the given root path.
 *
 * The path segments are expected to be relative paths although
 * providing an absolute path is also supported. In the case of an
 * absolute path segment this method will essentially only verify
 * that the absolute path is equal to or deeper in the directory
 * tree than the root path.
 *
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
```

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

**File:** app/src/lib/parse-app-url.ts (L10-24)
```typescript
export interface IOpenRepositoryFromURLAction {
  readonly name: 'open-repository-from-url'

  /** the remote repository location associated with the "Open in Desktop" action */
  readonly url: string

  /** the optional branch name which should be checked out. use the default branch otherwise. */
  readonly branch: string | null

  /** the pull request number, if pull request originates from a fork of the repository */
  readonly pr: string | null

  /** the file to open after cloning the repository */
  readonly filepath: string | null
}
```

**File:** app/src/lib/stores/app-store.ts (L7233-7258)
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
