### Title
Deep-link `filepath` parameter can escape the repository via prefix-matching flaw in `resolveWithin` - (File: `app/src/lib/path.ts`)

### Summary
The Sherlock report's root cause is a boundary/containment check that is computed incorrectly (summing raw votes instead of squared votes), letting an attacker satisfy a constraint that was only *textually* similar to the real one. GitHub Desktop has an analogous incorrect containment check in `_resolveWithin()`: it verifies that a resolved path is "inside" a root directory using a plain string `startsWith()` comparison instead of a path-segment-aware comparison, so a sibling directory that merely shares a name **prefix** with the repository root satisfies the check even though it is a different, unrelated directory.

### Finding Description
`_resolveWithin()` is the single guard Desktop uses to keep attacker-influenced relative paths confined to a repository: [1](#0-0) 

The final containment check is:
```
return realResolved.startsWith(realRoot) ? resolved : null
```
This is a plain string prefix comparison, not a path-boundary comparison. If `realRoot` is `/Users/victim/Documents/GitHub/my-repo` and an attacker-supplied relative path resolves (after `..` traversal) to `/Users/victim/Documents/GitHub/my-repo-secrets/id_rsa`, the check passes because the string `"...my-repo-secrets/id_rsa"` starts with `"...my-repo"` — even though `my-repo-secrets` is a completely different directory. The existing unit tests only cover parent-directory traversal (`..`, `../..`) and symlink escapes; they never test the sibling-directory-with-shared-prefix case, so this gap is unguarded: [2](#0-1) 

The most directly attacker-reachable caller is the custom-protocol deep-link handler. `parseAppURL()` accepts an untrusted `filepath` query parameter straight off the URL that the OS handed to Desktop when the user clicked a `x-github-client://openRepo/...` (or legacy `github-mac://`/`github-windows://`) link: [3](#0-2) 

That `filepath` only gets an `isAbsolute()` rejection and is otherwise passed unmodified into `resolveWithin(repository.path, filepath)`: [4](#0-3) 

Because `isAbsolute()` does nothing to stop `..`-relative paths, and `resolveWithin` only performs the flawed prefix check, a `filepath` value such as `..\my-repo-secrets\id_rsa` (or the POSIX equivalent) can resolve to any sibling directory whose name happens to start with the repository's directory name, and `shell.showItemInFolder(resolved)` will then reveal that arbitrary file to the attacker-controlled UI action.

The same primitive is reused for reading conflicted-file content that will be fed to Copilot conflict resolution: [5](#0-4) 

### Impact Explanation
This maps onto the allowed "file write or read outside the repo" impact category. An attacker who gets a victim to click a crafted `x-github-client://openRepo/<url>?filepath=..\<sibling-dir-prefix-match>\<secret file>` link can cause Desktop to reveal (via `shell.showItemInFolder`) or, in the Copilot conflict path, read into memory a file located outside the intended repository — as long as a directory sharing the repository's name as a prefix exists nearby (a realistic scenario for users who keep related/forked/backup clones side-by-side, e.g. `project` and `project-secrets`, `repo` and `repo.wiki`). This is a boundary-check defect, not a full sandbox bypass requiring local access or malware — it only requires the deep-link click, matching the "unprivileged... link or deep link the user clicks" attacker model.

### Likelihood Explanation
Exploitability depends on the coincidental existence of a sibling directory whose name is prefixed by the repository directory's name, which is a real but not universal condition (common with forked/backup repo naming conventions). The `isAbsolute()` guard blocks the trivial absolute-path case but does nothing against relative `..` traversal, and the code path is reachable purely through a URL the user clicks — no prior repository trust or local file-system access is required. Given the narrower precondition (naming coincidence) I'd rate this as a real but moderate-likelihood issue rather than universally exploitable.

### Recommendation
Fix the containment check in `_resolveWithin()` (`app/src/lib/path.ts`) to require an exact match or a path-separator boundary, e.g.:
```
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Add regression tests for the sibling-directory-prefix case (e.g. root `/tmp/foo` vs resolved `/tmp/foo-evil`) alongside the existing `..`/symlink tests in `app/test/unit/path-test.ts`.

### Proof of Concept
1. Victim has two local folders: `~/Documents/GitHub/my-repo` (a Desktop-tracked repository) and `~/Documents/GitHub/my-repo-secrets` (containing sensitive files, e.g. an unrelated private notes folder that happens to share the name prefix).
2. Attacker sends the victim a link:
   `x-github-client://openRepo/https://github.com/owner/my-repo?filepath=..%5Cmy-repo-secrets%5Csecret.txt`
3. Desktop parses the URL via `parseAppURL`, matches/opens the existing `my-repo` repository, then calls `openRepositoryFromUrl` with `filepath = "..\my-repo-secrets\secret.txt"`.
4. `isAbsolute(filepath)` is `false`, so the code proceeds to `resolveWithin(repository.path, filepath)`.
5. `resolveWithin` resolves to `~/Documents/GitHub/my-repo-secrets/secret.txt`, and because that string starts with `~/Documents/GitHub/my-repo`, the flawed check returns the path instead of `null`.
6. `shell.showItemInFolder(resolved)` opens/reveals `secret.txt`, a file outside the intended repository, purely as a result of the victim clicking the link.

Note: I was not able to fully verify from the index alone whether every relevant caller of `resolveWithin` (e.g. in `app-store.ts`) has additional upstream sanitization that would prevent reaching this code path with attacker-controlled traversal segments; a Devin session with full repo access would be needed to trace all call sites exhaustively before treating this as conclusively exploitable end-to-end.

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1972)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }

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

**File:** app/src/lib/copilot-conflict-context.ts (L388-401)
```typescript
      }

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
```
