## Title
Path‑containment check `resolveWithin` accepts sibling directories whose name is a string‑prefix of the root, allowing traversal outside the repository - (File: `app/src/lib/path.ts`)

### Summary
`resolveWithin`/`resolveWithinPosix`/`resolveWithinWin32` are the shared primitive Desktop uses to guarantee that a user‑ or remote‑supplied relative path stays inside a trusted root directory (a repository) before it is used to read a file or hand a path to the shell. The final containment test is a plain string‑prefix check with no path‑separator boundary, so a resolved real path whose name merely *starts with* the root's name — rather than being *inside* it — passes the check. This is the same bug class as the reported MetaMorpho issue: a boundary comparison that is "one character too loose," letting a value that should be rejected slip through a cap/containment check.

### Finding Description
The core of the containment check is: [1](#0-0) 

```js
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
```

`realResolved.startsWith(realRoot)` only checks that `realRoot` is a literal string prefix of `realResolved`; it does not require that the next character be a path separator (or that the strings be equal). Consequently, if a symlink or absolute path segment inside the syntactically‑valid, normalized path resolves (via `realpath`) to a *different* directory whose absolute path happens to start with the same characters as `realRoot` — e.g. root `/Users/victim/Documents/GitHub/project` vs. a sibling `/Users/victim/Documents/GitHub/project-secrets` or `project.bak` — the check returns success even though the target is outside the intended root.

This directly contrasts with the correct pattern used elsewhere in the same codebase for an analogous check, `isClonePathSensitive`, which explicitly appends the separator: [2](#0-1) 

`resolveWithin` is the security boundary relied on by multiple attacker‑reachable call sites:

- The `x-github-client://openRepo/...` deep‑link handler, where `filepath` is an attacker‑controlled query‑string value from a link the victim clicks, and the resolved path is fed to `shell.showItemInFolder`: [3](#0-2) 

- Reading file content for merge/conflict context, where `file.path` comes from the working tree of a repository that includes content merged in from an attacker‑controlled branch/PR: [4](#0-3) 

Existing unit tests only exercise the case where the symlink target is unrelated to the root (e.g. `/tmp/..`), which correctly fails; they do not cover the "sibling directory shares a prefix" case, so the gap is untested: [5](#0-4) 

### Impact Explanation
A successful bypass lets `resolveWithin` return a path that is outside the intended repository root while the caller believes it has been safely contained. Depending on the call site this can lead to:
- Revealing/opening files outside the repository via `shell.showItemInFolder` from a clicked deep link (info disclosure of file existence/location, and on some platforms revealing directory contents), and
- Reading arbitrary file content outside the repo into the Copilot conflict-resolution context, exfiltrating it into a model prompt, if a malicious branch/PR merge introduces a symlink whose target's real path coincides with a "root + suffix" sibling.

This matches the required impact class: an attacker who controls a cloned/fetched repository or a deep link the user clicks causes file read/behavior outside the intended repo boundary.

### Likelihood Explanation
Exploitation requires the victim's on‑disk layout to contain a directory whose absolute path is a literal prefix‑extension of the repository root (e.g. `project` / `project-old`, `repo` / `repo2`), which is a plausible but not universal precondition (common in developer environments with backup/clone-variant folders side by side). The defect itself, however, is unconditionally present in the shared primitive and is reachable from an unauthenticated deep link with no other prerequisite, so likelihood is opportunistic/environment‑dependent rather than universal.

### Recommendation
Change the containment check to require an exact match or a match followed by the path separator, mirroring the pattern already used in `isClonePathSensitive`:

```js
return (realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep))
  ? resolved
  : null
```

Add regression tests for sibling directories whose names share a prefix with the root (e.g. root `.../project` vs. symlink target `.../project-evil`) to lock in the fix.

### Proof of Concept
1. Victim has `~/Documents/GitHub/project` (a Desktop repository) and, elsewhere on disk, `~/Documents/GitHub/project-secrets/id_rsa`.
2. Inside `project`, a commit (from a malicious PR/branch the victim merges, or already tracked) contains a symlink `link -> ../project-secrets`.
3. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/victim/project?filepath=link%2Fid_rsa`.
4. Victim clicks the link; Desktop opens `project` and calls `resolveWithin(repository.path, "link/id_rsa")`.
5. `resolved` normalizes to `.../project/link/id_rsa`; `realpath` follows the symlink, producing `realResolved = ".../project-secrets/id_rsa"`.
6. `realResolved.startsWith(realRoot)` (`".../project-secrets/id_rsa".startsWith(".../project")`) evaluates `true`, so the path is treated as safely contained and passed to `shell.showItemInFolder`, revealing/opening `id_rsa` outside the repository. [1](#0-0) [3](#0-2)

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
