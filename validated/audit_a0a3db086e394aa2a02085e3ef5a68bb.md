## Title
Path-traversal boundary check in `resolveWithin` uses unanchored `startsWith`, allowing attacker-controlled deep-link `filepath` to escape the repository root - (File: `app/src/lib/path.ts`)

### Summary
The CREATE3 report's root cause is a broken invariant: code assumed a deterministic address-derivation guarantee ("this address will always correspond to the deployed contract") that silently fails on a different execution environment, letting an attacker-influenced value (the precomputed address) diverge from reality with no additional check to catch it. The Desktop analog is the containment guarantee in `resolveWithin`: its documentation promises the resolved path is "guaranteed to reside at, or underneath" the given root, but the actual boundary test is a plain string-prefix check with no path-separator anchoring, so the guarantee silently fails whenever a sibling directory shares a name prefix with the root.

### Finding Description
`resolveWithin` computes the final containment check as: [1](#0-0) 

`realResolved.startsWith(realRoot)` is a raw string comparison. If `realRoot` is `/Users/victim/Documents/GitHub/myrepo` and the resolved path is `/Users/victim/Documents/GitHub/myrepo-secrets/token.txt`, the `startsWith` check returns `true` even though `myrepo-secrets` is a completely different directory — there is no requirement that the character following the root be a path separator. This directly contradicts the function's own documented invariant that the result is guaranteed to reside "at, or underneath" `rootPath`.

This helper is relied upon as the sole traversal guard for attacker-influenced relative paths in at least two flows:

1. **Deep-link `filepath` handling** — `openRepositoryFromUrl` takes the `filepath` query parameter from an "Open in Desktop" URL (`x-github-client://openRepo/...?filepath=...`, parsed by `parseAppURL`) and passes it straight to `resolveWithin(repository.path, filepath)`, using only the boolean result to decide whether to reveal the file: [2](#0-1) 
The `filepath` field originates entirely from the attacker-crafted URL, with only an "is absolute" check performed beforehand: [3](#0-2) 

2. **Copilot conflict context** — `buildConflictContext` uses the same helper to gate file reads before sending file contents to the Copilot SDK: [4](#0-3) 

Because the guard's core boundary test is prefix-based rather than separator-anchored, any code path that reaches `resolveWithin` with an attacker-supplied relative path (containing `../` segments) can escape the intended root whenever a sibling path happens to extend the root's name, exactly the kind of "the safety invariant appeared to hold but silently didn't in this environment" failure mode described in the CREATE3 report.

### Impact Explanation
For the deep-link path, a successful escape causes `shell.showItemInFolder(resolved)` to reveal/open a file outside the cloned repository that the user never intended to expose via a link click — this is a read-outside-the-repo primitive triggered purely by the user clicking an "Open in Desktop" link, matching the "link or deep link the user clicks" attacker-controlled surface. For the Copilot conflict path, an escape could cause file content outside the repository to be read and forwarded into a Copilot prompt (a data-exfiltration-adjacent primitive), though the `file.path` there is normally repository-relative and less directly attacker-steerable than the deep-link `filepath`.

### Likelihood Explanation
Exploitation requires the attacker to guess or know a real sibling directory name on the victim's machine that shares a prefix with the repository's folder name (e.g., `myrepo` / `myrepo-backup`, `myrepo.old`, `myrepo2`) — this is a real constraint that reduces reliability compared to a universal traversal bug, but such prefix collisions are common in practice (backup copies, forked/renamed clones, or drive-root edge cases on Windows). The remainder of the path — reaching the vulnerable check via a single click on an "Open in Desktop" link — requires no local access, no elevated privileges, and no prior compromise, satisfying the in-scope unprivileged/deep-link criteria.

### Recommendation
Change the boundary check in `_resolveWithin` (`app/src/lib/path.ts`) to require a path-separator boundary, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
using the separator appropriate to the `options` module in use (`Path.sep`/`Path.win32.sep`/`Path.posix.sep`), so a resolved path can only be considered "within" the root when it is the root itself or nested strictly beneath it.

### Proof of Concept
1. Victim has cloned a repository into `/Users/victim/Documents/GitHub/myrepo` and, independently, has an unrelated folder `/Users/victim/Documents/GitHub/myrepo-secrets` (e.g., a backup or renamed clone) containing sensitive files.
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/owner/myrepo?filepath=..%2Fmyrepo-secrets%2Ftoken.txt`
3. `parseAppURL` parses this into an `open-repository-from-url` action with `filepath = "../myrepo-secrets/token.txt"`.
4. `openRepositoryFromUrl` calls `resolveWithin(repository.path, filepath)`. `resolve()` yields `/Users/victim/Documents/GitHub/myrepo-secrets/token.txt`, and `realResolved.startsWith(realRoot)` evaluates `true` because `myrepo-secrets` starts with `myrepo`, even though the file is outside the repository directory.
5. `shell.showItemInFolder(resolved)` is invoked, revealing `token.txt` from the unrelated directory in the OS file explorer — a disclosure that the code's own guard (and its documentation) claims is impossible.

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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

**File:** app/src/lib/parse-app-url.ts (L98-124)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
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
