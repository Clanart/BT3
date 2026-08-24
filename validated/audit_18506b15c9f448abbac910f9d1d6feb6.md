## Title
Incomplete path-containment check (`startsWith` without separator) in `resolveWithin` allows repository-content symlinks to smuggle files from outside the repo into privileged sinks - (File: `app/src/lib/path.ts`)

### Summary
`_resolveWithin()` is the app's single sanctioned guard against path traversal and symlink escapes when turning an attacker/repo-supplied relative path into an absolute one. Its final containment test is:

```ts
return realResolved.startsWith(realRoot) ? resolved : null
```

This is a string-prefix comparison with no trailing path-separator boundary check, so it treats any path whose *string* begins with `realRoot` as "inside" `realRoot`, even when it is really a sibling directory (e.g. `realRoot = ".../GitHub/myrepo"`, `realResolved = ".../GitHub/myrepo-secrets/token"`). This mirrors the report's pattern exactly: a guard that reads as a real invariant check but is structurally unable to reject the case it was written to reject. [1](#0-0) 

### Finding Description
`resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` are documented as guaranteeing the resolved path "reside[s] at, or underneath" `rootPath`, explicitly to defend against directory-traversal and symlink escapes. [2](#0-1) 

The implementation does correctly strip literal `..` traversal segments and null bytes before resolving, and it does call `realpath()` on both root and candidate to catch symlink escapes: [3](#0-2) 

But the final decision — `realResolved.startsWith(realRoot)` — is a pure substring test. It is missing the standard fix of comparing against `realRoot + path.sep`. Consequently, if a symlink *inside* the repository (fully attacker-controlled content, since the attacker authors the cloned/fetched repo) resolves to a real path that happens to share `realRoot` as a literal prefix but is actually a different, sibling directory, the function will treat it as "inside" and return the escaped absolute path as valid.

This is used as the sole traversal/symlink defense in two security-sensitive call sites:
1. `buildConflictContext()` in the Copilot merge-conflict-resolution feature, which resolves each conflicted file's repo-relative path with `resolveWithin(workingDirectory, file.path)`, then `stat()`s and `readFile()`s the result and folds its full content into the prompt sent to the configured AI model. [4](#0-3) 
2. `dispatcher.openRepositoryFromUrl()`, which resolves a `filepath` argument taken from an `x-github-client://openRepo` deep link against `repository.path` and then calls `shell.showItemInFolder(resolved)` on the result. [5](#0-4) 

Both sites rely entirely on `resolveWithin` returning `null` for anything outside the repository; neither re-validates the returned path.

### Impact Explanation
Because the attacker fully controls the contents of a repository they publish (a valid Desktop attacker primitive per the impact criteria), they can commit a symlink inside a tracked path (or a path that participates in a merge conflict) whose target — once resolved with `realpath()` — collides only at the string level with the victim's real clone path. When that happens, `resolveWithin` incorrectly reports the escaped, out-of-repo path as safe. In the Copilot conflict-resolution path, this results in the full contents of a file outside the repository boundary being read from disk and transmitted as part of the model prompt — a direct "file read outside the repo" primitive with subsequent exfiltration onto the network path to the model provider. In the deep-link path, it discloses the existence/location of files outside the repository via Explorer/Finder reveal. This satisfies the accepted impact category "file write or read outside the repo" driven by a repository the attacker controls.

### Likelihood Explanation
Exploitability is not universal — it requires the attacker's symlink target to resolve to a real, existing path that shares `realRoot`'s literal string as a prefix (e.g., a sibling clone folder whose name begins with the victim's repo folder name, such as `myrepo` vs. `myrepo-backup`, both commonly living under the default `~/Documents/GitHub/` clone base used by Desktop). This is a realistic but environment-dependent condition, not a guaranteed hit on every machine, which keeps likelihood at low/medium; however, the underlying guard is unconditionally broken for that class of layout, and no other layer of validation exists to catch it.

### Recommendation
Fix the containment check in `_resolveWithin` (`app/src/lib/path.ts`) to require a path-separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests mirroring the existing symlink-escape tests in `app/test/unit/path-test.ts`, specifically covering the sibling-directory-with-shared-prefix case, for both POSIX and Windows path option sets.

### Proof of Concept
1. Victim's Desktop default clone base is `~/Documents/GitHub/`, and the victim already has (or the attacker predicts) a directory `~/Documents/GitHub/notes-secret` containing sensitive files.
2. Attacker publishes a repository named `notes`; when cloned it lands at `~/Documents/GitHub/notes` (`realRoot`).
3. Attacker crafts a commit that produces a merge conflict where one of the conflicted file's parent path components is a symlink, e.g. `link -> ../notes-secret`, so that the path passed to `buildConflictContext` (`workingDirectory = ~/Documents/GitHub/notes`, `file.path = "link/token.txt"`) resolves via `realpath()` to `~/Documents/GitHub/notes-secret/token.txt`.
4. In `_resolveWithin`, `realResolved = ".../GitHub/notes-secret/token.txt"` and `realRoot = ".../GitHub/notes"`; `realResolved.startsWith(realRoot)` is `true` even though `notes-secret` is not nested inside `notes`.
5. `resolveWithin` returns the escaped absolute path instead of `null`; `buildConflictContext` proceeds to `stat`/`readFile` it and embeds its contents in the Copilot prompt sent off-repo. [1](#0-0) [4](#0-3)

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

**File:** app/src/lib/path.ts (L56-71)
```typescript
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
