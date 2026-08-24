### Title
`resolveWithin()` path-containment check uses bare `String.prototype.startsWith`, allowing sibling-directory/symlink escape past the repo-root boundary - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin()` (and its `posix`/`win32` variants) is the app's single sanitization primitive used to guarantee that a repository-relative path — often derived from data an attacker controls via a cloned/fetched repo (conflicted-file paths, on-disk symlinks) — cannot resolve outside the repository root before the app reads or writes it. The final containment check is a bare string comparison: [1](#0-0) 

`realResolved.startsWith(realRoot)` has no path-separator boundary check, so any real path whose string representation begins with the exact characters of `realRoot` — even a completely different, sibling directory such as `realRoot + "-something"` — passes the guard.

### Finding Description
`resolveWithin(rootPath, ...pathSegments)` resolves the caller-supplied path segments against the root, `realpath()`s both the root and the resolved candidate (to dereference symlinks), and then decides containment purely with:

```ts
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

This mirrors the H-6 bug class exactly: a computation/comparison that looks correct in isolation but is arithmetically/lexically off by the missing boundary term (there, a stray `1e18`; here, a missing path separator). If `realRoot` is `/Users/victim/proj` and a tracked symlink inside the repo resolves to `/Users/victim/proj-secrets/token.txt` (or any sibling path that happens to share `proj` as a string prefix), `startsWith` returns `true` even though the target is **not** inside the repo tree at all.

This helper is the load-bearing guard for multiple attacker-reachable flows where the file path or symlink target originates from a cloned/fetched repository:
- Reading conflicted-file content that's sent off-device to build AI context: `absolutePath = await resolveWithin(workingDirectory, file.path)` [3](#0-2) 
- Writing AI-resolved conflict content back to disk: `const absolutePath = await resolveWithin(repository.path, resolution.path)` followed by `await writeFile(absolutePath, resolution.resolvedContent, 'utf8')` [4](#0-3) 
- Resolving a deep-link `filepath` before revealing it in the OS file browser: `const resolved = await resolveWithin(repository.path, filepath)` [5](#0-4) 

Because `resolveWithin` returns the pre-realpath `resolved` value (not the dereferenced `realResolved`) once the check passes, a symlink committed inside the repo (e.g. `conflicted-file -> /Users/victim/proj-secrets/token.txt`) is accepted as "inside the root," and subsequent `readFile`/`writeFile` calls transparently follow that symlink to the real, out-of-repo target.

The project's own regression tests demonstrate the team is aware of and tests *some* symlink-escape cases, but none test the sibling-name/no-separator scenario: `fails for paths that use a symlink to traverse outside of the root` only covers a symlink to `../..`, which is caught incidentally because `realResolved` in that case does not share `realRoot` as a string prefix at all — not because the check has a separator boundary. [6](#0-5) 

### Impact Explanation
This breaks the exact invariant the app relies on to keep file I/O confined to the repository: "resolvability is decided... the file path is outside the repository." A malicious/compromised repository can plant a tracked symlink that, once cloned by the victim, causes GitHub Desktop to:
- **Read** arbitrary files outside the repo (whose real path happens to share the root's directory name as a string prefix) and feed their contents into the Copilot conflict-resolution context, an unintended read outside the repo boundary.
- **Write** attacker/AI-influenced content to a file outside the repo via the Copilot-resolution write path, i.e., corruption of files the user never intended to touch, driven entirely by content from an untrusted cloned repository.

This matches the "file write or read outside the repo" and "silent corruption of what the user commits" impact categories in scope.

### Likelihood Explanation
Exploitability depends on the victim having (or the attacker being able to induce) a sibling directory whose name is a prefix/superstring collision with the repository's directory name (e.g. `proj` vs `proj-secrets`, `app` vs `app-old`, or GitHub Desktop's own auto-suffixed clone folders like `repo`/`repo-1`/`repo (1)` produced when cloning a name that already exists locally). This is a plausible, not contrived, real-world layout, but it is not universally guaranteed — the attacker cannot fully choose the victim's local directory naming, only the symlink target relative to a guessed/likely sibling name. This keeps likelihood moderate rather than "trivial/always exploitable," but the missing separator check is a genuine, reachable defect in a security-relevant boundary function used across multiple sensitive I/O paths, not a hypothetical.

### Recommendation
Harden the containment check in `_resolveWithin` (`app/src/lib/path.ts`) to require an exact match or a match followed by the platform path separator:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add a regression test asserting that a symlink resolving to a sibling directory whose name is a string-prefix superset of the root (e.g. root `foo`, target `foo-evil`) is rejected.

### Proof of Concept
1. Attacker publishes a repository containing a tracked symbolic link `bait -> /home/victim/proj-secrets/token.txt` (or a relative equivalent that resolves there), where `proj-secrets` is a plausible sibling of a commonly-used clone directory name `proj`.
2. Victim clones the repo to `/home/victim/proj` in GitHub Desktop and gets it into a merge-conflict state that includes `bait` in `IFileConflictContext`.
3. `buildConflictContext` calls `resolveWithin('/home/victim/proj', 'bait')`; internally `realpath('/home/victim/proj/bait')` follows the symlink to `/home/victim/proj-secrets/token.txt`, and `'/home/victim/proj-secrets/token.txt'.startsWith('/home/victim/proj')` evaluates to `true` even though the target lives entirely outside the repo. [2](#0-1) [3](#0-2) 
4. The out-of-repo file's contents are read and included in the conflict-resolution context, and/or (in the Copilot write-back path) attacker/AI-influenced content is written back through the same symlink to the out-of-repo target via `writeFile(absolutePath, ...)`. [4](#0-3)

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

**File:** app/src/lib/copilot-conflict-context.ts (L390-401)
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
