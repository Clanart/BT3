### Title
`resolveWithin` uses a naive string-prefix check, allowing symlinks in a malicious repository to escape the working directory during Copilot conflict resolution - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin` (and its POSIX/Win32 variants) is Desktop's sole safeguard against path traversal and symlink escapes when reading/writing conflicted file content on behalf of a repository. Its containment check is a raw string `startsWith` comparison between the real (symlink-resolved) target path and the real root path, with no check that the match falls on a path-separator boundary. A sibling directory whose name merely starts with the repository's directory name (e.g. `myrepo-secrets` next to `myrepo`) will incorrectly satisfy the "inside root" check. A malicious repository can commit a symlink at a conflicted file path that targets such a sibling directory; when Desktop's Copilot conflict-resolution feature reads that path for AI context, or writes the AI's resolution back to that path, it will silently operate outside the intended repository.

### Finding Description
The containment check lives in `_resolveWithin`: [1](#0-0) 

```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`realpath` fully resolves symlinks, so `realResolved` can legitimately point anywhere on disk that a symlink chain leads to. The function then trusts a plain `String.prototype.startsWith` comparison to decide whether that resolved location is "inside" `realRoot`. This is the same broken-invariant pattern as the `Position` contract's `_maxBorrow` guard in the referenced report: a cheap, indirect proxy check (string prefix / token balance) is substituted for the actual security condition (directory containment / real collateral state) and can be defeated by an attacker who controls an adjacent piece of state (a sibling directory name / a token transfer).

Concretely, if the repository lives at `/Users/alice/Projects/myrepo` and the victim also has another local directory `/Users/alice/Projects/myrepo-secrets` (e.g. another cloned repo, a backup folder, or any directory Electron/git created), then:

- `realRoot = "/Users/alice/Projects/myrepo"`
- `realResolved = "/Users/alice/Projects/myrepo-secrets/id_rsa"`
- `"/Users/alice/Projects/myrepo-secrets/id_rsa".startsWith("/Users/alice/Projects/myrepo")` → `true`

There is no trailing separator check (`realResolved === realRoot || realResolved.startsWith(realRoot + sep)`), so the escape is not detected.

This function is the security boundary for exactly the flows the task requires — code that consumes attacker-controlled repository content:

- Reading conflicted files to build AI context, where `file.path` comes from git's conflict/status output for a merge the user is resolving (potentially against an attacker-supplied branch/PR): [2](#0-1) 

- Writing the AI-generated resolution content back to disk when the user confirms "Continue Merge": [3](#0-2) 

In both cases the only thing standing between "operate on a file inside the user's repo" and "operate on an arbitrary file that happens to sit in a same-prefixed sibling directory" is the broken `startsWith` check.

### Impact Explanation
- **Read path (`copilot-conflict-context.ts`)**: A conflicted file committed as a symlink pointing (via `../`) to a sibling directory can cause Desktop to read a file outside the repository and transmit its contents to the Copilot conflict-resolution model as "file content", resulting in exfiltration of secrets/credentials that happen to live in an adjacent, similarly-named directory (e.g. another project, a `.env`, SSH keys, or another cloned repo).
- **Write path (`app-store.ts` `_applyCopilotConflictResolutions`)**: Because the same broken check gates the write destination, the AI-generated (or attacker-influenced, since the model context itself can be attacker-poisoned via crafted conflict markers/PR text) resolution content can be written into a file outside the current repository, silently corrupting files in a sibling project the user never intended to touch.

This matches the accepted impact classes: file read outside the repo / credential exfiltration, and file write outside the repo / silent corruption of committed content — triggered purely by cloning/fetching and attempting to resolve a merge against an attacker-crafted repository, with no local/admin access or social engineering beyond the normal "resolve conflicts" workflow.

### Likelihood Explanation
Exploitation requires two conditions to align: (1) the victim resolves a conflict against a malicious repository/branch containing a symlinked conflicted file, and (2) a same-prefixed sibling directory exists next to the checked-out repository on the victim's disk. Condition 2 is not fully attacker-controlled, which somewhat limits reliability, but Desktop's own conventions (cloning multiple related repositories side-by-side, e.g. `org/repo` and `org/repo-private`, or `repo` and `repo-wiki`, under the same parent folder) make prefix collisions realistic in practice, and the check's failure mode is silent (no error, no warning) rather than fail-closed. The underlying defect — a security check using unguarded `startsWith` instead of separator-aware containment — is present in `app/src/lib/path.ts` today, independent of how likely a given victim's directory layout is to trigger it, the same way the referenced Solidity report's flaw existed regardless of how likely a specific griefing transaction was to be sent.

### Recommendation
Fix `_resolveWithin` to require a path-boundary match, not just a string prefix:

```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```

(with the appropriate `sep` from the passed-in `options` for the POSIX/Win32 variants). Add a regression test with a sibling directory whose name is a superstring of the root directory's name (e.g. root `foo`, sibling `foo-evil`) to ensure `resolveWithin` rejects symlinks/paths resolving into it.

### Proof of Concept
1. Victim has `~/Projects/myrepo` (a normal cloned repo) and `~/Projects/myrepo-secrets/id_rsa` (any pre-existing sibling directory/file, e.g. another checkout).
2. Attacker crafts `myrepo`'s remote/PR so that a merge introduces a conflicted file `leak` that is committed as a symlink: `leak -> ../myrepo-secrets/id_rsa`.
3. Victim fetches/merges and opens "Resolve with Copilot" on the conflict.
4. `buildConflictContext` calls `resolveWithin(workingDirectory, "leak")`:
   - `resolved` follows the symlink via `realpath` to `~/Projects/myrepo-secrets/id_rsa`.
   - `realRoot = "~/Projects/myrepo"`; `realResolved.startsWith(realRoot)` is `true` because `"myrepo-secrets"` starts with `"myrepo"`.
   - The function returns a non-null path instead of `null`, so the file is read and its content is included in the request sent to the Copilot model — reading data outside `myrepo`.
5. Symmetrically, if `resolution.path` for a Copilot-suggested fix happens to resolve through such a symlink, `_applyCopilotConflictResolutions`'s `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` writes outside the repository at `~/Projects/myrepo-secrets/id_rsa`. [4](#0-3) [2](#0-1) [3](#0-2)

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
