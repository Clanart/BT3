## Analysis

The report's core issue is a **broken invariant in a boundary/containment check** — a value meant to keep something safely bounded (a fee proportion) is computed with the wrong scale, silently weakening the guarantee. The closest genuine analog in GitHub Desktop is not a math-scaling bug (Desktop has no fee logic), but the same *bug class*: a containment check that looks correct but has an off-by-boundary flaw, allowing something the check was meant to keep inside a boundary to actually escape it.

That analog exists in the path-containment helper `resolveWithin`, which is relied on to keep deep-link-provided file paths inside a repository.### Title
Path-containment check uses unbounded `startsWith` prefix comparison, allowing deep-link `filepath` to escape the repository into a sibling directory - ([File: app/src/lib/path.ts])

### Summary
`resolveWithin()` (`app/src/lib/path.ts`) is Desktop's single shared primitive for confining an attacker/URL-influenced relative path to a repository root. Its final containment test is:

```
return realResolved.startsWith(realRoot) ? resolved : null
``` [1](#0-0) 

`startsWith` does a raw string-prefix comparison with no path-separator boundary check. Just like the HATS report's root cause — a constant used at the wrong scale silently weakens an intended guarantee — this check silently weakens the intended "must be inside root" guarantee: any resolved path whose string representation merely begins with the same characters as the root (e.g. a sibling directory `…/repo-backup` vs. root `…/repo`) is incorrectly treated as "inside" the repo.

### Finding Description
This helper is reachable from an unprivileged, attacker-controlled input: the `x-github-client://openRepo/...&filepath=...` deep link.

`parseAppURL` extracts `filepath` from the query string with **no traversal or path validation** (only `pr` and `branch` are regex-checked) [2](#0-1) .

`dispatcher.openRepositoryFromUrl` only rejects `filepath` if it is an absolute path; a relative path containing `..` segments is passed straight to `resolveWithin`:

```
if (isAbsolute(filepath)) { log.error(...); return }
const resolved = await resolveWithin(repository.path, filepath)
if (resolved !== null) { shell.showItemInFolder(resolved) }
``` [3](#0-2) 

Inside `resolveWithin`, `Path.resolve(normalizedRoot, normalizedRelative)` correctly walks `..` segments out of the root before the final check [4](#0-3) . If the repository is checked out at, say, `/Users/victim/Documents/GitHub/Hello-World`, and the attacker's `filepath` is `../Hello-World-notes/secret.txt`, the resolved absolute path becomes `/Users/victim/Documents/GitHub/Hello-World-notes/secret.txt` — a sibling directory, *not* a subpath of the repo. The containment check then computes:

```
"/Users/victim/Documents/GitHub/Hello-World-notes/secret.txt".startsWith(
  "/Users/victim/Documents/GitHub/Hello-World")
// => true, because "Hello-World-notes" textually begins with "Hello-World"
```

Because there's no check that the character immediately after the shared prefix is a path separator (or that the strings are equal), the function returns the sibling path as "resolved" instead of `null`, and the caller treats it as validated.

### Impact Explanation
`shell.showItemInFolder(resolved)` reveals/opens whatever file the attacker specified in the file manager, as long as a sibling directory happens to share the repo's name as a prefix — a realistic condition since Desktop itself names clone destinations after the repository name (e.g., users routinely have `repo`, `repo-old`, `repo2`, `repo-backup` side by side in their default GitHub folder). This is a path-containment bypass triggered purely by a link the user clicks, matching the report's "unprivileged, attacker-controls-a-deep-link" impact class: file disclosure/read outside the intended repository boundary. `resolveWithin` is a general-purpose primitive also used elsewhere (e.g. conflict-context file reading) [5](#0-4) , so any future or existing caller that trusts its `null`-vs-path result to be a strict containment guarantee inherits the same escape.

### Likelihood Explanation
Requires no special privileges, malware, or credentials — only that the victim clicks a crafted `x-github-client://openRepo/...&filepath=...` link (or the legacy `github-mac`/`github-windows` protocols) for a repository they have already cloned, and that a same-prefixed sibling directory exists, which is a common real-world naming pattern. The `isAbsolute` guard gives a false sense of security while doing nothing to stop `..`-based relative escapes combined with the prefix bug.

### Recommendation
Fix `_resolveWithin` to require an exact match or a trailing separator boundary, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Apply the same fix to `resolveWithinPosix`/`resolveWithinWin32` (shared implementation) and add regression tests using sibling-directory names that share a prefix (as already partially covered for `sanitizeCloneName`/clone-path tests, but missing for `resolveWithin` itself).

### Proof of Concept
1. Victim has previously cloned `https://github.com/owner/Hello-World` into `~/Documents/GitHub/Hello-World`, and separately has a folder `~/Documents/GitHub/Hello-World-secrets` containing sensitive files (a common pattern: backups, exported notes, `-old`/`-v2` folders, etc.).
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/owner/Hello-World?filepath=..%2FHello-World-secrets%2Fcredentials.txt`
3. Victim clicks the link. Desktop's `parseAppURL` passes `filepath = "../Hello-World-secrets/credentials.txt"` through unchecked [6](#0-5) .
4. `openRepositoryFromUrl` sees `isAbsolute(filepath) === false`, so it proceeds to `resolveWithin(repository.path, filepath)` [7](#0-6) .
5. `resolveWithin` resolves to `~/Documents/GitHub/Hello-World-secrets/credentials.txt`, and the flawed `startsWith` check treats this as contained within `~/Documents/GitHub/Hello-World`, returning it as valid.
6. `shell.showItemInFolder(resolved)` opens the victim's file explorer directly at the attacker-chosen file outside the actual repository, disclosing its existence/location without any warning that the path fell outside the repo.

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

**File:** app/src/lib/parse-app-url.ts (L98-125)
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
