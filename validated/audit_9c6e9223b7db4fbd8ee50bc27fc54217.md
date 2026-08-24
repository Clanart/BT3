### Title
TOCTOU race between path-containment check and file read allows arbitrary file read via symlinked conflicted file - (File: `app/src/lib/copilot-conflict-context.ts`)

### Summary
The Llama report's broken invariant is that a security-relevant precondition (`action.minExecutionTime` not yet passed) is checked once, but the actual privileged operation (`execute`) happens later, after the state that made the check valid can have silently changed — creating an exploitable window. GitHub Desktop's Copilot conflict-resolution path has the same structural flaw: a path-containment guard is evaluated once via `resolveWithin`, but the file is then read through separate, later filesystem calls, leaving a window in which the on-disk object backing the already-validated path can be swapped.

### Finding Description
`buildConflictContext` validates each conflicted file path with `resolveWithin(workingDirectory, file.path)`, which resolves symlinks with `realpath` and confirms the result is still inside the repository root: [1](#0-0) 

After that check succeeds, the function performs two more, separate filesystem operations on the *same path string* — `stat(absolutePath)` and then `readFile(absolutePath, 'utf8')`: [2](#0-1) 

`resolveWithin` itself only guarantees safety at the instant it runs — it calls `realpath` once and compares prefixes, but does not hold any lock or file descriptor across the gap to the caller's later I/O: [3](#0-2) 

Because these are three independent async filesystem operations (`realpath` inside `resolveWithin`, then `stat`, then `readFile`), a symlink can be swapped into place at `file.path` (inside the working directory) *after* the containment check passes but *before* `readFile` executes, redirecting the actual read to an arbitrary location outside the repository (e.g. `~/.ssh/id_rsa`, `.aws/credentials`, or another user's files). The repository is fully attacker-controlled content in this flow (a malicious branch merged/rebased/cherry-picked, which is exactly when conflicted files and this code path are populated), so the attacker chooses which repo-relative paths become conflicted and can arrange for one of them to be replaced by a symlink at exactly the right moment, e.g. by having a hook that fires on Desktop's frequent background `git status`/fsmonitor calls (enumerated and proxied elsewhere in the codebase via `getRepoHooks`/`withHooksEnv`) toggle the file between a regular file and a symlink.

### Impact Explanation
If the race wins, `readFile` returns the contents of a file outside the repository under the attacker's chosen path. Per the module's own documentation, this content is assembled into `ICopilotConflictContext` specifically "suitable for sending to the Copilot SDK," so a successful race can leak the content of an arbitrary, attacker-chosen file the Desktop process can read (credentials, SSH keys, other repos) into a request sent to Copilot's backend — an unprivileged, repository-controlled path to read-outside-repo/exfiltration, matching the impact categories in scope (file read outside the repo, credential exfiltration).

### Likelihood Explanation
This requires precise timing (a genuine TOCTOU race across three separate `fs` calls with no cross-process lock), so it is not trivially reliable, and I was not able to confirm within this codebase a concrete mechanism (e.g., a git hook proven to be invoked concurrently with `buildConflictContext`) that gives the attacker a reliable trigger to win the race — this is the main open uncertainty. The existing test suite in `app/test/unit/path-test.ts` demonstrates `resolveWithin` correctly rejects symlink escapes *evaluated at a single point in time*, which is exactly why this gap is easy to miss: the unit test proves the check works, but does not (and structurally cannot) prove the guarantee still holds at the moment `readFile` runs afterward.

### Recommendation
Eliminate the check-then-use gap by opening the file once and validating on the open file descriptor: use `fs.open`/`fs.promises.open` with `O_NOFOLLOW` (where available) or perform `lstat` immediately before `readFile` and reject if the path is a symlink, then do the realpath-based containment check and the read against the same resolved file descriptor rather than re-touching the path string multiple times. Alternatively, read the file once (fd-based) and derive both the size and the content from that single open handle so no additional resolution occurs after the safety check.

### Proof of Concept
1. Set up a repository with a merge/rebase conflict on file `secret-link.txt` inside the working directory.
2. Arrange (e.g., via a git hook triggered by Desktop's background status polling, or any process with write access to the working tree during the operation) for `secret-link.txt` to alternate rapidly between: (a) a regular file with conflict markers, and (b) a symlink pointing to `~/.ssh/id_rsa`.
3. Trigger Desktop's "Resolve with Copilot" flow, which calls `buildConflictContext`.
4. `resolveWithin(workingDirectory, 'secret-link.txt')` executes while state (a) is in effect and returns the in-repo absolute path. [4](#0-3) 
5. Before the subsequent `stat`/`readFile` calls execute, the hook flips the file to state (b) (symlink to `~/.ssh/id_rsa`).
6. `readFile(absolutePath, 'utf8')` follows the symlink and returns the private key content, which is folded into `rawContent`/hunks and passed onward toward the Copilot SDK request. [5](#0-4) 

**Note on confidence:** I could not verify, from local index searches alone, the exact downstream call that transmits `ICopilotConflictContext` off-device, nor a proven concurrent hook-trigger mechanism during this specific code path — these would need to be confirmed with full repository access (e.g., a Devin session) before treating this as a fully confirmed, weaponizable vulnerability rather than a design-level TOCTOU weakness.

### Citations

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

**File:** app/src/lib/copilot-conflict-context.ts (L409-438)
```typescript
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
