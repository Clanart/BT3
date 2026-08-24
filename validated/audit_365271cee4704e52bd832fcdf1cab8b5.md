## Title
`resolveWithin()` path-containment check uses unanchored prefix match, allowing escape to sibling directories - ([File: app/src/lib/path.ts])

## Summary
`resolveWithin()` is GitHub Desktop's central guard against path traversal (used to sandbox file access to a repository's working directory). Its final containment check is `realResolved.startsWith(realRoot)`, with no trailing path-separator anchoring. This is the same class of bug as the OUSD report: a boundary/validation check that looks correct in the common case but has an edge condition (missing exact-boundary enforcement) that lets the guarded operation exceed its intended scope — in OUSD's case "one token more than the balance," here "one directory more than the intended root."

## Finding Description
`resolveWithin()` in [1](#0-0)  resolves the target path, `realpath()`s both the root and the resolved target, and then does:
```
return realResolved.startsWith(realRoot) ? resolved : null
```
Because `realRoot` is not suffixed with `path.sep` before the comparison, any real path that merely shares `realRoot` as a *string prefix* — not as a proper parent directory — passes the check. For example, if the repository root is `/Users/victim/Documents/GitHub/app` and a sibling directory `/Users/victim/Documents/GitHub/app-secrets` (or `app2`, `app.bak`, `application`, etc.) exists on disk, then `realResolved = /Users/victim/Documents/GitHub/app-secrets/config.json` satisfies `startsWith(realRoot)` even though it is not inside the repository at all.

This function is the app's designated defense against "path traversal and symlink escapes," and it is used, among other places, in:
- `buildConflictContext()` in [2](#0-1)  to sandbox reading of files listed as merge-conflict participants before their raw content is sent to an external Copilot/AI conflict-resolution API.
- `openRepositoryFromUrl()` in [3](#0-2) , where the `filepath` query parameter of an `x-github-client://openRepo/...` deep link (fully attacker-controlled, parsed in [4](#0-3) ) is resolved against the repository root before being handed to `shell.showItemInFolder()`.

In both cases the code assumes `resolveWithin` returning non-null guarantees the path is inside the repository. It does not — it only guarantees a shared string prefix. The existing symlink-aware `realpath()` resolution (verified by the test suite in [5](#0-4) ) correctly defeats simple `..`/symlink traversal, but the missing separator anchor means the fix is incomplete: it stops "traverse-then-come-back" tricks but not "traverse-into-a-similarly-named-sibling" tricks.

## Impact Explanation
For `buildConflictContext`, a successful bypass causes the contents of a file outside the repository (in a sibling directory that shares the repo directory name as a prefix) to be read and forwarded as `rawContent`/hunks to Copilot's conflict-resolution API — an out-of-repo file read plus exfiltration of that content to an external network service. For `openRepositoryFromUrl`, it lets an attacker-supplied deep-link `filepath` cause Desktop to reveal (via Explorer/Finder) a file outside the intended repository, disclosing its existence/location. Both fall inside the accepted impact categories ("credential/token exfiltration" / "file... read outside the repo") since the attacker vector is either a maliciously crafted conflicting merge (from a cloned/fetched repository) or a deep link the user clicks — no local access, admin rights, or pre-existing malware required.

## Likelihood Explanation
Exploitation is conditioned on a sibling directory whose name has the repository directory's name as a prefix already existing next to the repository (a real-world but not universal directory layout, e.g. `repo` / `repo-old`, `app` / `app2`, `project` / `project-backup`). This precondition reduces reliability compared to a fully general traversal bug, but it is a plausible and common enough naming pattern that the missing separator check is a genuine, exploitable regression of the containment guarantee `resolveWithin` is documented to provide, and it silently defeats the "Guard against path traversal and symlink escapes" comment at the `buildConflictContext` call site.

## Recommendation
Anchor the containment check to an exact path boundary, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests asserting that a sibling directory sharing the root's name as a string prefix (e.g. root `.../app`, sibling `.../app-evil`) is rejected by `resolveWithin`, `resolveWithinPosix`, and `resolveWithinWin32`.

## Proof of Concept
1. Layout on disk: `/Users/victim/Documents/GitHub/app` (the tracked repository) and `/Users/victim/Documents/GitHub/app-secrets/config.json` (an unrelated sibling directory containing sensitive data).
2. Attacker pushes/prepares a branch that, when merged by the victim, produces a merge conflict whose conflicted entry is (or resolves through) a symlink pointing to `../app-secrets/config.json`.
3. Victim triggers Copilot-assisted conflict resolution; `buildConflictContext()` calls `resolveWithin(workingDirectory, file.path)` in [6](#0-5) .
4. `_resolveWithin` in [1](#0-0)  computes `realResolved = /Users/victim/Documents/GitHub/app-secrets/config.json` and `realRoot = /Users/victim/Documents/GitHub/app`; `realResolved.startsWith(realRoot)` is `true`, so the null-guard is bypassed and `config.json`'s contents are read and forwarded as part of the AI conflict context.

Note: I could not directly confirm from the index how `file.path` for `buildConflictContext` is populated end-to-end (e.g., whether git's conflict-listing logic normalizes/rejects symlink-typed conflict entries before they reach this function), since that call chain lives in `app-store.ts` and I only had partial visibility into it. If you need the exact upstream data flow verified, a Devin session with full repository access would be required to trace it precisely.

### Citations

**File:** app/src/lib/path.ts (L66-71)
```typescript
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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
