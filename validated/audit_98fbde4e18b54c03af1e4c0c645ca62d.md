### Title
Path-containment check in `resolveWithin()` uses a bare `startsWith()` prefix test, allowing symlink/deep-link escapes to write or exfiltrate files outside the repository - (File: app/src/lib/path.ts)

### Summary
`_resolveWithin()` is Desktop's central "stay inside the repo" guard. It is meant to guarantee that a path derived from untrusted input (a conflicted-file path coming from a cloned/fetched repository, or a `filepath` parameter from an `x-github-client://` deep link) can never resolve to a location outside the intended root directory. The final decision is made with a plain string prefix comparison that does not require a path-separator boundary after the root, so a resolved path whose *directory name* merely starts with the root's directory name (e.g. `…/repo-secrets` vs root `…/repo`) is incorrectly treated as "inside" the repo. This mirrors the reported `IgnitionCore.sol` class of bug: a boundary/threshold check that fails to fully account for the value being compared, letting the guarded condition be violated at least once.

### Finding Description
`_resolveWithin()` in [1](#0-0)  computes the final containment verdict as:

```
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

`startsWith(realRoot)` is a raw string-prefix test. It does not check that the character immediately following `realRoot` in `realResolved` is a path separator (or that the strings are exactly equal). Consequently, if `realRoot` is `/Users/victim/Projects/myrepo` and `realResolved` is `/Users/victim/Projects/myrepo-secrets/notes.txt`, the check passes even though `myrepo-secrets` is a completely different directory that merely shares the literal prefix `myrepo`.

`resolved` itself is produced by `resolve(normalizedRoot, normalizedRelative)` [3](#0-2) , which lexically applies any `..` segments in the supplied relative path before the `realpath`/`startsWith` check is used purely to defeat *symlink* escapes (the lexical `..` case is otherwise unconstrained by `resolve()`). The function's own doc comment and the unit tests explicitly frame this as the "symlink escape" defense [4](#0-3) , but the existing tests only exercise a symlink that points far outside the tree (`tmpdir()/..`) — a case where the prefix mismatch is obvious. They never exercise the adjacent-directory/shared-prefix case, so the missing separator-boundary check went unnoticed.

Two attacker-reachable call sites make this exploitable:

1. **Copilot merge-conflict resolution** – `buildConflictContext()` uses `resolveWithin(workingDirectory, file.path)` to sandbox conflicted-file paths taken straight from git's working directory status before reading file content into memory to send to the AI backend: [5](#0-4) . If the malicious/attacker-controlled repository being merged introduces a symlink whose target's real path lands in a sibling directory sharing the repo's directory name as a prefix (a realistic layout, e.g. `repo` and `repo-backup`, `repo` and `repo2`, `project` and `project-secrets`), `resolveWithin` will wrongly certify the path as "inside" the repo, and the guard reads that file's content: [6](#0-5) .

2. **Writing the AI resolution back to disk** – after the model returns a resolution, `AppStore` re-derives the path with the same `resolveWithin(repository.path, resolution.path)` check and then writes the AI-generated content directly to that path: [7](#0-6) . Because the containment check can be bypassed the same way, this becomes an **arbitrary file write outside the repository**, silently corrupting a file the user never intended to touch.

3. **Deep-link driven file open** – `Dispatcher.openRepositoryFromUrl()` also relies on `resolveWithin(repository.path, filepath)` to validate a `filepath` argument parsed straight out of an `x-github-client://openRepo` deep link before revealing it in Explorer/Finder: [8](#0-7) . A crafted deep link can supply a lexical `../` traversal that lands in a sibling folder sharing the repo folder's name as a prefix, causing Desktop to reveal/point at a file the attacker chose outside the repository, entirely from an unprivileged link click.

### Impact Explanation
- File content from outside the cloned repository (potentially secrets, credentials, or unrelated project files sitting in an adjacent directory) can be read and transmitted off-box as part of the Copilot conflict-resolution prompt — this is a credential/token or sensitive-file exfiltration path, one of the explicitly valid impact categories.
- The write-back path in `app-store.ts` turns the same flaw into an unauthorized file write outside the intended repository directory, i.e. "silent corruption" of content the user did not choose to modify, driven entirely by content in an attacker-supplied/malicious repository being merged.
- The deep-link path lets a remote attacker (anyone who gets the victim to click a crafted `x-github-client://` link) direct Desktop's file-reveal action outside the repo, without any local/physical access or prior compromise, satisfying the "link or deep link the user clicks" attacker model.

### Likelihood Explanation
Exploiting this reliably requires a coincidence of directory naming: the resolved out-of-root path must literally begin with the root path's characters (a sibling directory or file whose name starts with the repo's folder name). This is a real-world common pattern (`repo`/`repo2`, `repo`/`repo-old`, `repo`/`repo.bak`, forks cloned side-by-side) but is not guaranteed for every victim, so likelihood is environment-dependent rather than universal. Nonetheless it requires no elevated privileges, no pre-existing malware, and no unnatural user steps beyond the normal Desktop workflows (resolving a merge conflict with Copilot, or clicking a deep link), and the existing regression tests do not cover this exact boundary case, so the gap is unpatched in current code.

### Recommendation
Change the containment check in `_resolveWithin()` to require an exact match or a full path-segment boundary, e.g.:

```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```

Add regression tests where `realRoot` and `realResolved` share a directory-name prefix but are actually siblings (e.g. `root = /tmp/xyz/repo`, target = `/tmp/xyz/repo-secret/file`) to ensure the fix is exercised, mirroring how the original report recommended including the boundary condition explicitly in the check rather than relying on an incomplete comparison.

### Proof of Concept
1. Victim has (or an attacker can predict/cause) two adjacent directories, e.g. `~/Documents/GitHub/project` (the tracked repo) and `~/Documents/GitHub/project-notes` (containing a sensitive file `notes.txt`).
2. Attacker crafts/pushes a branch that, when merged, produces a conflicted file which is actually a symlink named e.g. `evil` pointing to `../project-notes/notes.txt`.
3. Victim opens Desktop, hits a merge conflict, and uses "Resolve with Copilot". `buildConflictContext()` calls `resolveWithin('~/Documents/GitHub/project', 'evil')`; `realpath` resolves through the symlink to `~/Documents/GitHub/project-notes/notes.txt`; `realResolved.startsWith(realRoot)` evaluates true because `"…/project-notes/notes.txt".startsWith("…/project")` is `true`, even though `project-notes` is not inside `project`.
4. The content of `notes.txt` is read [6](#0-5)  and included in the request sent to the Copilot backend, and/or the AI's "resolution" for that path is subsequently written back to `~/Documents/GitHub/project-notes/notes.txt` via `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` [9](#0-8) , overwriting a file outside the repository the user never selected.

### Citations

**File:** app/src/lib/path.ts (L53-71)
```typescript
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
```

**File:** app/test/unit/path-test.ts (L65-78)
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

**File:** app/src/lib/copilot-conflict-context.ts (L429-438)
```typescript
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
