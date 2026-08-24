### Title
Sibling-directory path-traversal bypass in `resolveWithin` allows escape from repository root via crafted deep-link filepath - ([File: app/src/lib/path.ts])

### Summary
`resolveWithin` in `app/src/lib/path.ts` is Desktop's central guard for "is this path inside the repository root," used to sanitize attacker-influenced relative paths before performing filesystem operations. Its final check uses a raw string-prefix comparison (`realResolved.startsWith(realRoot)`) with no path-separator boundary check. This is the same bug class as the reported airdrop issue: a boundary/equality check that is too loose (`<=` instead of `<` in the report; a bare `startsWith` instead of a separator-aware comparison here), which lets an attacker-supplied index/path "match" something it shouldn't.

### Finding Description
`_resolveWithin` (app/src/lib/path.ts:36-72) computes:
```
const resolved = resolve(normalizedRoot, normalizedRelative)
const realRoot = await realpath(normalizedRoot)
const realResolved = await realpath(resolved)
return realResolved.startsWith(realRoot) ? resolved : null
``` [1](#0-0) 

`String.prototype.startsWith` treats `realRoot` as a plain character prefix, not a directory boundary. If the repository root is, e.g., `/Users/bob/Documents/GitHub/app` and a sibling directory exists that shares this string as a prefix (e.g. `/Users/bob/Documents/GitHub/app-secrets` or `/Users/bob/Documents/GitHub/app2`), then a resolved path such as `/Users/bob/Documents/GitHub/app-secrets/id_rsa` will satisfy `realResolved.startsWith(realRoot)` even though it is **not** inside `app`. The existing unit tests only exercise `..`-outside-root and symlink-escape cases, never the same-prefix-sibling case, so this gap is untested and unguarded. [2](#0-1) 

This guard is relied upon directly in the deep-link ("Open in Desktop") handler:
```
if (filepath !== null) {
  if (isAbsolute(filepath)) { ... return }
  const resolved = await resolveWithin(repository.path, filepath)
  if (resolved !== null) {
    shell.showItemInFolder(resolved)
  } else { ... }
}
``` [3](#0-2) 

The `filepath` value originates from an `x-github-client://` / `github-mac://openRepo/...?filepath=...` URL parsed by `parseAppURL`, i.e. fully attacker-controlled content that a user is enticed to click. [4](#0-3) 

The same `resolveWithin` primitive is also used to gate file reads for Copilot conflict-resolution context building (`readFile(absolutePath, 'utf8')`) and file writes when applying Copilot's proposed resolutions (`writeFile(absolutePath, resolution.resolvedContent, 'utf8')`): [5](#0-4) [6](#0-5) 

Because the boundary check is the sole gate protecting all of these operations, any caller that constructs a sibling path whose name prefix-matches the repository directory name defeats the intended containment.

### Impact Explanation
An attacker who controls a deep-link URL (or, more speculatively, a conflicting-path value reaching the Copilot resolution path) can cause Desktop to read or write files outside the intended repository root once the resolved path happens to fall inside a sibling directory that shares a name prefix with the repository folder. Depending on the call site this yields:
- Disclosure of a file's location/name outside the repo via `shell.showItemInFolder` (deep-link path).
- Silent write of attacker/AI-influenced content to a file outside the repository root via the Copilot conflict-resolution `writeFile` path, corrupting unrelated files the user did not intend to touch.

This matches the report's core theme: a boundary check that looks correct but is subtly wrong, letting an attacker-influenced value pass a guard it should fail.

### Likelihood Explanation
Exploitation requires a sibling directory to exist whose absolute path is a string-prefix superset of the repository root (e.g., `repo` vs `repo-backup`, `repo` vs `repo2`, or GHE-org naming conventions with shared prefixes). This is a plausible but not universal precondition — it depends on the victim's local directory layout (Desktop's default clone location is `~/Documents/GitHub/<name>`, so prefix collisions across cloned repos are realistic, e.g. `octocat` and `octocat-private`). No admin rights, local access, or pre-existing malware is required — only that the victim click a link and that a prefix-colliding sibling directory exist. This lowers confidence to "moderate" likelihood rather than "high," since I could not verify from the index alone how often such sibling-name collisions occur in real installations, nor fully trace whether `resolution.path` in the Copilot write path can be made to contain `../` sequences (git normally rejects `..` in tracked paths, which would limit that particular sink).

### Recommendation
Fix `_resolveWithin` to perform a directory-boundary-aware comparison instead of a raw string prefix check, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests in `app/test/unit/path-test.ts` covering the sibling-directory-prefix case (e.g. root `/tmp/foo`, target `/tmp/foobar/x`) for both POSIX and Windows path modules.

### Proof of Concept
1. Victim has cloned two repositories into the default location such that one directory name is a prefix of another, e.g. `~/Documents/GitHub/octocat` and `~/Documents/GitHub/octocat-private` (the latter containing a sensitive file `secret.txt`).
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/octocat/octocat?filepath=../octocat-private/secret.txt`
3. Victim clicks the link. Desktop resolves/opens `octocat`, then calls `resolveWithin('/Users/victim/Documents/GitHub/octocat', '../octocat-private/secret.txt')`.
4. `resolved` becomes `/Users/victim/Documents/GitHub/octocat-private/secret.txt`; `realResolved.startsWith(realRoot)` is `true` because the string `"...GitHub/octocat-private/secret.txt"` starts with `"...GitHub/octocat"`.
5. `shell.showItemInFolder(resolved)` reveals/opens the file outside the intended repository, even though the guard is supposed to prevent exactly this (`app/src/ui/dispatcher/dispatcher.ts:1957-1972`).

### Citations

**File:** app/src/lib/path.ts (L64-71)
```typescript
  // Resolve to an absolute path. Note that this will not contain
  // any directory traversal segments.
  const resolved = resolve(normalizedRoot, normalizedRelative)

  const realRoot = await realpath(normalizedRoot)
  const realResolved = await realpath(resolved)

  return realResolved.startsWith(realRoot) ? resolved : null
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
