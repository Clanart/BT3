## Analysis

The OpenVM report's broken invariant is: **a bounds/containment check was applied, but it was not tight enough to prevent the checked value from crossing into forbidden territory** — the byte-wise limb check didn't stop the full value from exceeding the field modulus.

The GitHub Desktop analog exists in the path-containment primitive `resolveWithin`, which is used throughout the app to guarantee that an attacker/AI-controlled relative path segment cannot escape a trusted root directory. [1](#0-0) 

### Title
Path-containment bypass via prefix-matching in `resolveWithin` allows writes/reads outside repository root - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin()` is Desktop's core guard for ensuring a resolved path stays inside a given root directory. It validates containment with `realResolved.startsWith(realRoot)`, a plain string-prefix comparison with no check that the next character is a path separator. Because of this, any sibling directory whose name textually begins with the root directory's name (e.g. root `.../GitHub/repo` and sibling `.../GitHub/repo-secrets`) is incorrectly treated as "inside" the root. [2](#0-1) 

### Finding Description
`_resolveWithin` normalizes the root and the caller-supplied path segments, resolves them together with `Path.resolve`, and then checks containment purely by string prefix: `realResolved.startsWith(realRoot)`. [3](#0-2) 

If an attacker-controlled relative path segment contains `..` traversal that lands in a sibling folder whose name happens to share the root's name as a prefix (e.g. `repository.path` = `/Users/victim/Documents/GitHub/repo` and a sibling `/Users/victim/Documents/GitHub/repo-backup` or `repo-secrets` exists), the resolved absolute path (e.g. `/Users/victim/Documents/GitHub/repo-secrets/foo.txt`) will satisfy `startsWith(realRoot)` even though it is a completely different directory. This is exactly analogous to the OpenVM bug: a partial/byte-level constraint (`startsWith` on a truncated boundary) fails to fully constrain the value (the path) to the intended range (strictly under `root/`).

This helper is consumed in security-sensitive, attacker-reachable call sites:
- `app-store.ts`, in the Copilot conflict-resolution flow, where `resolution.path` (part of an AI/extension-provided conflict resolution list) is passed straight into `resolveWithin(repository.path, resolution.path)` and, if it returns non-null, the resolved path is written to disk with attacker-controlled content via `writeFile(absolutePath, resolution.resolvedContent, 'utf8')`. [4](#0-3) 
- `dispatcher.ts`, in `openRepositoryFromUrl`, where `filepath` originates from an externally clicked deep link and is resolved with `resolveWithin(repository.path, filepath)` before being passed to `shell.showItemInFolder(resolved)`. [5](#0-4) 

### Impact Explanation
In the Copilot-resolution path, a crafted `resolution.path` such as `../repo-secrets/foo.txt` (where a directory literally named with the repo name as a prefix exists next to the repository, e.g. another cloned repo or a Desktop-created folder) bypasses the "resides under repository root" guard and causes Desktop to silently write attacker-controlled file content outside the intended repository, potentially overwriting files in a sibling project. This matches the requested impact class: "silent corruption of what the user commits" / "file write outside the repo," driven by content that ultimately derives from an untrusted repository's conflict state processed by the resolution pipeline.

### Likelihood Explanation
The prefix bug is deterministic and requires no symlinks or filesystem races — it is a pure string-comparison flaw triggered whenever a sibling path name shares the root's name as a prefix, which is a common occurrence when users keep multiple related clones (`repo`, `repo-backup`, `repo-old`, `repo2`) in the same parent folder (Desktop's default clone location groups all repos under one base directory). The `..`-based traversal segment needed to reach the sibling is a normal relative path, not requiring symlink creation, so the existing `resolveWithin` containment check is bypassed on the first call. [6](#0-5) 

### Recommendation
Fix `_resolveWithin` to require a path-separator boundary (or exact equality) after the prefix match, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
This mirrors the OpenVM fix of tightening the range check (constraining the top limb to 5 bits) so the checked value cannot straddle the boundary of the intended range.

### Proof of Concept
```ts
// Given:
//   repository.path        = /Users/victim/Documents/GitHub/repo
//   sibling directory       = /Users/victim/Documents/GitHub/repo-secrets  (pre-existing)
//
// Attacker-influenced Copilot resolution list contains:
const resolution = {
  path: '../repo-secrets/payload.txt',
  resolvedContent: '<attacker content>',
}

// app-store.ts flow:
const absolutePath = await resolveWithin(repository.path, resolution.path)
// _resolveWithin computes:
//   resolved     = /Users/victim/Documents/GitHub/repo-secrets/payload.txt
//   realRoot     = /Users/victim/Documents/GitHub/repo
//   realResolved.startsWith(realRoot) === true   // BUG: "repo-secrets" starts with "repo"
// => absolutePath is NOT null, containment check is bypassed

await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
// writes attacker-controlled content to /Users/victim/Documents/GitHub/repo-secrets/payload.txt,
// entirely outside the intended repository directory.
``` [4](#0-3) [3](#0-2)

### Citations

**File:** app/src/lib/path.ts (L36-71)
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
