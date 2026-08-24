## Title
Symlink TOCTOU in `resolveWithin` lets a malicious repository redirect conflict-file reads outside the working directory before they are sent to Copilot - ([File: app/src/lib/copilot-conflict-context.ts])

### Summary
The external report's core primitive is a validate-then-use gap: a value is validated once, but the action that matters happens later, in a separate step an attacker can influence in between. `resolveWithin` in [1](#0-0)  reproduces that exact shape for filesystem paths: it resolves symlinks with `realpath` only at check time and then hands back the *unresolved* `resolved` path, which is re-resolved by the OS on every subsequent `fs` call. `buildConflictContext` in [2](#0-1)  calls `resolveWithin` once, then performs a separate `stat` and `readFile` on the same path afterward — a check/use split with two intervening `await`s.

### Finding Description
`resolveWithin(rootPath, ...pathSegments)` computes `resolved` via `Path.resolve`/`join`, then calls `realpath` on both the root and the resolved path to confirm the *real* target is inside the root, but ultimately returns `resolved` (the pre-`realpath` path), not `realResolved`: [3](#0-2) 

`buildConflictContext` uses this helper to sandbox conflicted-file reads to the repository root before feeding file content to Copilot: [2](#0-1) 

The file paths being resolved (`file.path`) come from the merge/rebase/cherry-pick conflict list of an in-progress operation against a repository the user cloned/fetched — i.e. an attacker who authored one side of the conflicting history fully controls what object exists at that repository-relative path, including a symlink. Because the safety check is evaluated once and the actual `stat`/`readFile` calls re-resolve the path from disk independently afterward, there is a window between the `resolveWithin` check (line 393) and the `readFile` call (line 431) where the on-disk target of a path component can change. If anything mutates the working tree during that window — e.g. a submodule operation, a background checkout, or another async git/file operation racing with conflict-context gathering — a symlink that resolved safely inside the repo at check time can resolve to a location outside the repo at read time, and `readFile` will happily follow it.

This mirrors the report's "front-running" class exactly: the guard (`sponsorProposal`'s implicit assumption / here `resolveWithin`'s realpath check) is sound in isolation, but splitting validation and action across two independent operations reopens the very race the guard was meant to close.

### Impact Explanation
If the race is won, `buildConflictContext` reads arbitrary file content from outside the repository (anything readable by the Desktop process) and includes it as `rawContent` in `IConflictResolutionContext`, which is subsequently sent off-repo to the Copilot backend for conflict resolution. This is a read-outside-the-repo / exfiltration primitive: local files unrelated to the repository (credentials, SSH keys, other project source) could be smuggled into an outbound AI request via a maliciously crafted symlink placed by a conflicting commit authored by an attacker.

### Likelihood Explanation
This requires winning a narrow TOCTOU race between `resolveWithin`'s internal `realpath` and the later `stat`/`readFile`, and requires some concurrent filesystem mutation (e.g., an overlapping submodule/checkout operation, or the attacker's own tooling racing a filesystem watcher) to flip the symlink target during that window. It is not a trivially reliable single-click exploit, but it does not require local/admin access beyond what "clone a malicious repository and trigger a merge conflict with Copilot resolution" already implies — the attacker-controlled input is entirely inside the cloned repository content. I was not able to fully verify, within the available index, whether any additional concurrent code paths in this codebase reliably create the mutation window (e.g., whether submodule updates or other file writes run concurrently with `buildConflictContext`); this is the main uncertainty in the likelihood assessment.

### Recommendation
Apply the "pull pattern" analog here: resolve once and use the resolved, canonical path throughout — i.e., have `resolveWithin` return `realResolved` (or have callers immediately re-derive all subsequent operations, `stat`, `readFile`, etc., by opening a file descriptor via `realpath`-verified path and reusing that descriptor/fd for all following reads instead of the string path) so there's no second, independent resolution step an attacker can race. At minimum, `buildConflictContext` should open the file once (e.g., via `fs.open`+`fstat`+`read`) immediately after validation rather than validating a path string and later re-resolving it via separate `stat`/`readFile` calls.

### Proof of Concept
Not independently verified against a running build; conceptually: 
1. Attacker crafts a branch whose merge with the victim's branch produces a text conflict at path `docs/notes.md`, and in the same commit/checkout that path is (or becomes) a symlink pointing to a benign in-repo target so `resolveWithin` at line 393 succeeds.
2. During the small async gap between the `resolveWithin` check and the `stat`/`readFile` calls, something on disk swaps that symlink's target to point outside the repository (e.g., `/Users/victim/.ssh/id_rsa` or another sensitive path) — this requires a concurrent write, which is the unverified assumption in this analog.
3. `readFile(absolutePath, 'utf8')` follows the now-swapped symlink and returns the outside-repo file content as `rawContent`, which flows into the Copilot conflict-resolution request payload. [4](#0-3) [5](#0-4)

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

**File:** app/src/lib/copilot-conflict-context.ts (L390-438)
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
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }
```
