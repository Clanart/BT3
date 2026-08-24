### Title
Path-containment check in `resolveWithin` uses unbounded `String.startsWith`, allowing sibling-directory escape via a symlink in an attacker-controlled repository - ([File: app/src/lib/path.ts])

### Summary
The reported swapnet issue is a "broken invariant" bug: a single constant/comparison (`Common.DECIMALS`) is assumed to be universally valid, but the assumption silently fails for a class of inputs (non-18-decimal tokens), letting an attacker bypass a security-critical boundary (the collateral check) by orders of magnitude. The GitHub Desktop analog is structurally identical: the containment check in `resolveWithin` assumes that a string-prefix match (`realResolved.startsWith(realRoot)`) is equivalent to "is a descendant of," but that invariant silently breaks whenever a sibling path happens to share the root path as a string prefix (e.g. `/repo` vs `/repo-secret`). This lets a crafted repository (via a symlink) escape the intended repository boundary while still passing the "is inside the working directory" guard.

### Finding Description
`resolveWithin` in [1](#0-0)  is the canonical safety gate used across the app to ensure that a path derived from repository content ultimately resolves to somewhere inside the repository working directory. It normalizes and resolves the path, follows symlinks with `realpath`, and then performs the containment check: [2](#0-1) 

The check `realResolved.startsWith(realRoot)` is a bare string-prefix comparison with no trailing path-separator boundary check. This is the same class of flaw as the original report: a "generic" comparison (`startsWith`, analogous to a hardcoded `Common.DECIMALS`) is treated as always correct, but it silently fails for a specific, attacker-reachable edge case — when `realResolved` is a *sibling* directory whose name happens to begin with the exact characters of `realRoot` (for example `realRoot = "/Users/victim/Projects/myrepo"` and `realResolved = "/Users/victim/Projects/myrepo-secrets/passwords.txt"`). In that case `"/Users/victim/Projects/myrepo-secrets/...".startsWith("/Users/victim/Projects/myrepo")` evaluates to `true`, even though the resolved path is not actually inside `myrepo` at all.

The attacker fully controls the value that gets resolved: they can commit a symlink inside the cloned/fetched repository whose target is an arbitrary absolute path. Once the user clones such a repository and `realpath` resolves the symlink to a real, existing sibling path that shares the root's directory name as a prefix, the boundary check is bypassed.

`resolveWithin` is relied upon as the sole traversal/symlink guard in at least these call sites:
- `buildConflictContext` in [3](#0-2) , which reads file content from the resolved path and sends it to an AI model.
- Copilot conflict-resolution application in [4](#0-3) , which calls `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` on the resolved path.
- Usage in `app/src/ui/dispatcher/dispatcher.ts` (3 call sites), also gating path resolution against the repository root.

The comment in `buildConflictContext` explicitly frames `resolveWithin` as the sole defense: *"Guard against path traversal and symlink escapes (cross-platform)"* — [5](#0-4) . Because the boundary test itself is flawed, this stated guarantee does not hold for the sibling-prefix case.

### Impact Explanation
If an attacker crafts a repository containing a symlink at some tracked path whose target is an absolute path on the victim's filesystem that happens to share the repository root's name as a string prefix, `resolveWithin` will incorrectly treat the resolved (out-of-repo) path as "inside the repository." Depending on which call site is reached this yields either:
- **File read outside the repo**: the out-of-repo file's contents are read into `buildConflictContext` and forwarded to the Copilot conflict-resolution model, constituting exfiltration of file content outside the intended repo boundary.
- **File write outside the repo**: `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` in the copilot-resolution apply path can write attacker-influenced content to a file outside the repository, corrupting or overwriting unrelated files on disk.

Both outcomes match the requested impact classes (file read/write outside the repo) and are reached purely by the victim cloning/fetching a malicious repository and Desktop automatically walking conflict files — no elevated privileges or local access are required beyond normal repository use.

### Likelihood Explanation
Exploitation requires: (1) the attacker crafts a repository containing an in-repo symlink pointing to an absolute out-of-repo target, and (2) a real directory/file exists on the victim's disk whose absolute path is a string-prefix superset of the cloned repository's root path (e.g. `<clone-root>-something`). Condition (2) is not guaranteed for arbitrary victims, which limits blind, universal exploitation, but it is a realistic condition to target deliberately (e.g. default clone locations such as `~/Documents/GitHub/<repo>` create predictable sibling-name collisions such as `~/Documents/GitHub/<repo>-old`, `<repo>-backup`, `<repo>2`, etc., which attackers can probe for or which are common in practice). The root cause itself — an unbounded `startsWith` used as a path-containment check — is a deterministic, code-level defect independent of any external assumption, so likelihood should be assessed as the reachability of the boundary condition rather than the correctness of the containment logic, which is unconditionally broken for this input class.

### Recommendation
Fix the boundary check in `_resolveWithin` ( [2](#0-1) ) to require an exact match or a prefix match followed by the path separator:

```ts
return realResolved === realRoot ||
  realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```

Apply the equivalent fix to any other place performing manual path-containment checks via `startsWith` against a root path, and add regression tests that specifically cover a sibling directory sharing the root's name as a prefix (e.g. `root = "/tmp/foo"`, target = `/tmp/foo-evil/secret`).

### Proof of Concept
1. On a POSIX system, create a directory to act as the "victim" clone root and a sibling directory sharing its name as a prefix, containing a sensitive file:
   ```
   mkdir -p /tmp/myrepo
   mkdir -p /tmp/myrepo-secrets
   echo "TOP SECRET" > /tmp/myrepo-secrets/passwords.txt
   ```
2. Inside `/tmp/myrepo` (representing the cloned malicious repository), create a symlink whose target is the sibling file:
   ```
   ln -s /tmp/myrepo-secrets/passwords.txt /tmp/myrepo/link
   ```
3. Call the vulnerable function directly (mirroring `app/test/unit/path-test.ts`):
   ```ts
   import { resolveWithin } from '../../src/lib/path'
   const result = await resolveWithin('/tmp/myrepo', 'link')
   console.log(result) // returns a path, NOT null, even though it points to /tmp/myrepo-secrets
   ```
   Because `realpath('/tmp/myrepo/link')` resolves to `/tmp/myrepo-secrets/passwords.txt`, and `"/tmp/myrepo-secrets/passwords.txt".startsWith("/tmp/myrepo")` is `true`, the function returns a non-null resolved path instead of `null`, incorrectly certifying the target as being inside `/tmp/myrepo`.
4. In the real app, replace `link` with a path referenced by a merge-conflicted file tracked in the malicious repository; `buildConflictContext` ( [3](#0-2) ) will read `/tmp/myrepo-secrets/passwords.txt` and include its content in the payload sent to the Copilot conflict-resolution feature, and/or the apply-resolution path ( [4](#0-3) ) will write attacker/model-influenced content back to that same out-of-repo file.

### Citations

**File:** app/src/lib/path.ts (L36-72)
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
}
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
