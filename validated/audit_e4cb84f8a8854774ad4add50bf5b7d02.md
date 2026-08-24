## Title
Broken directory-containment check in `resolveWithin()` allows escaping the repository root via sibling-named folders — (File: `app/src/lib/path.ts`)

## Summary
`resolveWithin()` is Desktop's central guard against path traversal: it is supposed to guarantee that a resolved path is "at, or underneath" a given root directory. The final containment test uses a raw string-prefix comparison, `realResolved.startsWith(realRoot)`, with no check that a path-separator (or exact equality) follows the shared prefix. [1](#0-0) 

This is the exact same bug class as the ENS `BytesUtils.equals` finding: a "greater-or-equal / prefix" check is used where an exact boundary/length match is required, so a string that merely *starts with* the expected value is treated as if it *equals* (or is strictly nested under) it.

## Finding Description
`_resolveWithin` computes `resolved` by joining/resolving `rootPath` with attacker-influenced `pathSegments`, then calls `realpath` on both root and resolved path and finally checks:

```ts
return realResolved.startsWith(realRoot) ? resolved : null
``` [2](#0-1) 

Because `startsWith` performs a literal character-prefix comparison, any resolved path whose string representation happens to begin with `realRoot` — even a completely different, sibling directory such as `<root>-secrets`, `<root>.bak`, or `<root>2` — passes the check. The function's own doc comment claims the result "is guaranteed to reside at, or underneath this path," which is false for such inputs. [3](#0-2) 

Notably, another part of the codebase gets this right: `isClonePathSensitive` in `app/src/lib/git/clone.ts` explicitly appends `Path.sep` before comparing (`clonePath.startsWith(sensitive + Path.sep)`), showing the developers are aware of the correct pattern elsewhere but failed to apply it in `resolveWithin`. [4](#0-3) 

The unit tests for `resolveWithin` never exercise the sibling-prefix case (only `..`, null bytes, and symlink escapes are tested), so this gap is not caught. [5](#0-4) 

## Impact Explanation
`resolveWithin` is used as the sole containment guard when handling the `filepath` parameter of the `x-github-client://openRepo/...` deep link — an attacker-controlled link the user clicks. The handler explicitly blocks absolute paths but relies entirely on `resolveWithin` to stop `..`-based escapes: [6](#0-5) 

If a `filepath` such as `../<repoName>-secrets/secret.txt` resolves (after `realpath`) to a sibling folder whose name shares the repository directory name as a literal prefix — a very plausible layout under Desktop's default clone root (e.g. `~/Documents/GitHub/project` next to `~/Documents/GitHub/project-secrets` or `project.bak`) — the check incorrectly passes and `shell.showItemInFolder(resolved)` is invoked on a path outside the intended repository, revealing/exposing file-system content the user never intended to expose from that link.

The same broken primitive also guards which on-disk files get read and forwarded to the Copilot merge-conflict context builder: [7](#0-6) 

This matches the report's valid-impact criteria: attacker controls a deep link the user clicks, resulting in file access outside the repo boundary, with no local/physical access, admin rights, or pre-existing malware required.

## Likelihood Explanation
Exploitation requires only that the victim click a crafted `x-github-client://openRepo/...&filepath=...` link and that a directory sharing a name-prefix with the target repository exists on disk — a common and unforced naming pattern for developers (backup folders, "-old", "-v2", "-secrets", numbered variants, etc.), making this a realistic path an attacker can attempt with off-the-shelf social links rather than contrived local conditions.

## Recommendation
Require an exact match or a proper separator boundary after the shared prefix, mirroring the pattern already used in `isClonePathSensitive`:

```diff
- return realResolved.startsWith(realRoot) ? resolved : null
+ return realResolved === realRoot || realResolved.startsWith(realRoot + sep)
+   ? resolved
+   : null
```
where `sep` is the separator appropriate to the `options` module in use (`Path.win32.sep` / `Path.posix.sep`), so the fix also honors the `resolveWithinPosix` variant.

## Proof of Concept
1. Victim has GitHub Desktop cloned repos laid out as `~/Documents/GitHub/project` and `~/Documents/GitHub/project-secrets` (containing e.g. `.env`, credential dumps, or another private repo) — a normal, unforced local layout.
2. Attacker sends: `x-github-client://openRepo/https://github.com/owner/project?filepath=..%2Fproject-secrets%2F.env`.
3. `parseAppURL` parses this into `{ name: 'open-repository-from-url', url: 'https://github.com/owner/project', filepath: '../project-secrets/.env' }` [8](#0-7) .
4. `dispatcher.openRepositoryFromUrl` opens the `project` repository; `isAbsolute(filepath)` is `false`, so it calls `resolveWithin(repository.path, '../project-secrets/.env')` [9](#0-8) .
5. Inside `resolveWithin`: `resolved = /Users/victim/Documents/GitHub/project-secrets/.env`, `realRoot = /Users/victim/Documents/GitHub/project`. `realResolved.startsWith(realRoot)` evaluates to `true` because the string `"...project-secrets/.env"` literally starts with `"...project"`, even though the file is not under `project` at all.
6. The guard incorrectly returns the resolved path, and `shell.showItemInFolder(resolved)` opens the OS file browser at the sensitive sibling file, exposing content outside the intended repository — triggered purely by the victim clicking the link.

### Citations

**File:** app/src/lib/path.ts (L13-24)
```typescript
/**
 * Resolve one or more path sequences into an absolute path underneath
 * or at the given root path.
 *
 * The path segments are expected to be relative paths although
 * providing an absolute path is also supported. In the case of an
 * absolute path segment this method will essentially only verify
 * that the absolute path is equal to or deeper in the directory
 * tree than the root path.
 *
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
```

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
```

**File:** app/src/lib/git/clone.ts (L40-44)
```typescript
  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }
```

**File:** app/test/unit/path-test.ts (L44-63)
```typescript
  describe('resolveWithin', () => {
    const root = process.cwd()

    it('fails for paths outside of the root', async () => {
      assert((await resolveWithin(root, join('..'))) === null)
      assert((await resolveWithin(root, join('..', '..'))) === null)
    })

    it('succeeds for paths that traverse out, and then back into, the root', async () => {
      assert.equal(await resolveWithin(root, join('..', basename(root))), root)
    })

    it('fails for paths containing null bytes', async () => {
      assert((await resolveWithin(root, 'foo\0bar')) === null)
    })

    it('succeeds for absolute relative paths as long as they stay within the root', async () => {
      const parent = resolve(root, '..')
      assert.equal(await resolveWithin(parent, root), root)
    })
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
