## Analysis

The Velodrome report's core defect is a **boundary/prefix check computed incorrectly**, letting a value that belongs to the wrong "bucket" (next epoch instead of previous) pass validation. Searching GitHub Desktop for the same bug class — a boundary comparison that is off by exactly the delimiter it should account for — surfaces the path-containment helper `_resolveWithin` in `app/src/lib/path.ts`, which is the app's central defense against path traversal, and it has exactly this class of flaw: it checks a string prefix without requiring the path-separator boundary that would make the containment check sound.

### Title
Missing path-separator boundary check in `resolveWithin()` allows partial-prefix path traversal outside the repository - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin()` is the shared helper GitHub Desktop uses to guarantee that a repository-relative path (frequently derived from attacker-influenceable input such as an `x-github-client://` deep link or AI-conflict-resolution output) stays inside a given root directory. Its containment check uses a raw string `startsWith()` comparison without verifying that the next character after the root is a path separator, so a sibling directory whose name merely begins with the root directory's name is incorrectly treated as "inside" the root.

### Finding Description
The relevant logic: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`realResolved.startsWith(realRoot)` is true not only when `realResolved` is `realRoot` or a true descendant of it, but also when `realResolved` is a completely different, sibling path that merely shares the same string prefix (e.g. `realRoot = /Users/victim/Documents/GitHub/repo` and `realResolved = /Users/victim/Documents/GitHub/repo-secrets/token.json`). No trailing separator is appended to `realRoot` before the comparison, so the "boundary" between "inside root" and "outside root" is computed incorrectly — the exact same class of error as the report's off-by-epoch-boundary index (a value that logically belongs to a different bucket is accepted because the boundary math omits the delimiter).

Notably, the codebase shows the correct pattern exists elsewhere for a related but separate check, `isClonePathSensitive()` in `app/src/lib/git/clone.ts`, which explicitly appends the separator: [2](#0-1) 

This confirms the separator-boundary requirement is understood in this codebase, but `resolveWithin()` — the function actually reused across multiple security-sensitive call sites — does not apply it.

`resolveWithin()` is used to gate:
- Deep-link initiated file reveal, where `filepath` comes directly from an `x-github-client://` URL that a user can be tricked into clicking: [3](#0-2) 
- Writing AI/Copilot-proposed conflict resolutions to disk: [4](#0-3) 
- Reading conflicted-file content into an AI context payload: [5](#0-4) 

### Impact Explanation
Where `resolveWithin()` gates a **write** (the Copilot conflict-resolution acceptance path) or a **reveal/read** (the deep-link `filepath` handler), a bypass lets a crafted path escape the intended repository root as long as a sibling directory or file on disk happens to share the same name prefix as the repository directory. This satisfies "file write or read outside the repo" from a link the user clicks or from attacker-influenceable content, without any admin rights, local access, or social engineering beyond a normal deep-link click.

### Likelihood Explanation
Exploitation requires the existence of a second directory alongside the repository whose name is a superstring of the repository directory's name (e.g. `repo` vs `repo-config`), which is a real but not universal filesystem layout (GitHub Desktop by default clones repositories into a shared parent folder like `Documents/GitHub/`, where multiple repos with prefix-overlapping names commonly coexist). This mirrors the original report's "Medium likelihood" rating — the flaw is deterministic and always triggerable in the vulnerable code path once the naming precondition holds, but the precondition is not guaranteed for every victim.

### Recommendation
Change the containment check to require a full path-segment boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
This mirrors the fix pattern already used in `isClonePathSensitive()` and closes the sibling-directory bypass for every caller of `resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32`.

### Proof of Concept
1. Victim has two folders under the same parent: `/Users/victim/Documents/GitHub/repo` (a tracked Desktop repository) and `/Users/victim/Documents/GitHub/repo-secrets/token.json` (an unrelated sensitive file/directory).
2. Attacker sends the victim a crafted deep link matching the repo's remote URL with `filepath=..%2Frepo-secrets%2Ftoken.json` (a relative, non-absolute path so it bypasses the `isAbsolute` guard in `openRepositoryFromUrl`).
3. `resolveWithin(repository.path, filepath)` resolves to `/Users/victim/Documents/GitHub/repo-secrets/token.json`; `realResolved.startsWith(realRoot)` is `true` because `"…/repo-secrets/token.json".startsWith("…/repo")`, even though `repo-secrets` is not inside `repo`.
4. `shell.showItemInFolder(resolved)` is called on a path outside the repository, confirming the boundary check is bypassable; the same bypass pattern applies to the Copilot conflict-resolution `writeFile` call site if a conflicting file path can be made to resolve to a sibling-prefixed location.

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

**File:** app/src/lib/git/clone.ts (L40-44)
```typescript
  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
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
