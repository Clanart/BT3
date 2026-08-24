### Title
Path-containment check in `resolveWithin` uses unbounded `startsWith`, allowing deep-link `filepath` to escape into sibling directories - (File: `app/src/lib/path.ts`)

### Summary
The `_resolveWithin` helper in `app/src/lib/path.ts`, which is the sole guard used to keep the `filepath` parameter of an `x-github-client://openRepo/...` deep link confined to the cloned repository, validates containment with `realResolved.startsWith(realRoot)`. This check has no path-separator boundary, so any sibling directory whose name is prefixed by the repository's directory name (e.g. `repo` vs `repo-secrets`) is incorrectly treated as "inside" the root. An attacker who controls a deep link (or a repo clone URL a victim opens through "Open in Desktop") can craft a `filepath` query value that traverses out of the repo and back into a same-prefixed sibling folder, causing Desktop to reveal/open a file outside the cloned repository.

### Finding Description
`app/src/lib/path.ts:36-72` implements the containment check: [1](#0-0) 
`resolved` is computed by joining/resolving the (attacker-controlled) path segments against the root, and the only safety check is `realResolved.startsWith(realRoot)`. This is the classic CWE-22 "partial path/prefix" bypass: `"/Users/x/repo-secrets/config".startsWith("/Users/x/repo")` is `true`, even though `repo-secrets` is a completely different, sibling directory, not a subdirectory of `repo`.

This function is reached from the deep-link handler in `app/src/ui/dispatcher/dispatcher.ts`: [2](#0-1) 
The only pre-check performed before calling `resolveWithin` is `isAbsolute(filepath)`, which rejects absolute paths but does nothing to stop a relative path such as `../repo-secrets/config.json`. The `filepath` value itself originates unsanitized (beyond generic query-string extraction) from the URL handled by `parseAppURL`: [3](#0-2) 
Note that `branch` is validated with `testForInvalidChars`, but `filepath` has no equivalent validation — it is passed straight through.

The existing unit tests for `resolveWithin` (`app/test/unit/path-test.ts:44-101`) only exercise: plain `..` escape (correctly rejected), traverse-out-then-back-into-the-same-root (correctly accepted), null bytes, and symlink escapes. None of them cover the "traverse out then into a same-prefixed sibling directory" case, which is exactly the gap the `startsWith` check leaves open.

### Impact Explanation
A successful exploit causes Desktop to call `shell.showItemInFolder(resolved)` on an attacker-chosen path outside the cloned repository: [4](#0-3) 
This reveals the existence and location of a file that lives outside the repository root — a violation of the intended sandbox (root containment) that `resolveWithin` is explicitly documented and relied upon to enforce ("the resolved path is guaranteed to reside at, or underneath, this path"). Depending on what a victim has on disk under a same-prefixed sibling directory (e.g. another repo, a backup folder, credentials/config folders that happen to share a name prefix with a repo the victim has cloned), this can disclose the presence/path of sensitive files to be revealed in the OS file explorer, i.e. a file-read/disclosure outside the repo boundary that the report's Desktop-analog scope explicitly calls out as valid impact ("attacker controls ... a link or deep link the user clicks ... and the result is ... file write or read outside the repo").

### Likelihood Explanation
The attack requires no local access, admin rights, or leaked credentials — only that the victim click a maliciously crafted `x-github-client://openRepo/...` link (or an "Open in Desktop" button on a malicious/compromised web page) pointing at a repo URL the victim has already cloned (or is willing to clone), with a `filepath` query parameter containing a `..`-based relative path that lands in a same-name-prefixed sibling directory. The `isAbsolute()` guard gives a false sense of safety while leaving the relative-traversal-to-sibling path fully open, so likelihood is not negligible: it only depends on the naming coincidence between the target repo directory and some other directory on the victim's disk (which is a realistic occurrence for personal projects, e.g., `project` vs. `project-backup`, `project-old`, `project.bak`, etc., or an attacker instructing the victim beforehand to create such a sibling folder as part of a social step — though that additional step would weaken the "no unnatural user steps" criterion and should be weighed by the triager).

### Recommendation
Fix `_resolveWithin` in `app/src/lib/path.ts` to require a directory-separator boundary (or exact equality) when checking containment, e.g.:
```ts
return realResolved === realRoot || realResolved.startsWith(realRoot + Path.sep)
  ? resolved
  : null
```
Apply the same fix to the win32/posix variants that reuse `_resolveWithin`. Additionally, consider validating `filepath` in `parseAppURL` (similar to the `testForInvalidChars` check already applied to `branch`) to reject path segments containing `..` outright before they ever reach `resolveWithin`.

### Proof of Concept
1. Victim has previously cloned `https://github.com/some/project` to `~/Documents/GitHub/project`, and separately has an unrelated folder `~/Documents/GitHub/project-secrets` containing sensitive files (a plausible naming coincidence, or one an attacker could induce via a prior benign-looking suggestion, e.g., a README instructing them to create a "-secrets" folder for local overrides).
2. Attacker crafts and gets the victim to click:
   `x-github-client://openRepo/https://github.com/some/project?filepath=..%2Fproject-secrets%2Fconfig.json`
3. `parseAppURL` (`app/src/lib/parse-app-url.ts:98-124`) parses this into `{ name: 'open-repository-from-url', url: 'https://github.com/some/project', filepath: '../project-secrets/config.json' }`.
4. `openRepositoryFromUrl` in `dispatcher.ts` resolves/opens the existing `project` repository, then calls `isAbsolute(filepath)` — which is `false` for `../project-secrets/config.json`, so execution proceeds to `resolveWithin(repository.path, filepath)`.
5. Inside `_resolveWithin`, `resolved = resolve('~/Documents/GitHub/project', '../project-secrets/config.json')` = `~/Documents/GitHub/project-secrets/config.json`. `realResolved.startsWith(realRoot)` evaluates `'~/Documents/GitHub/project-secrets/config.json'.startsWith('~/Documents/GitHub/project')` → `true`.
6. `resolveWithin` incorrectly returns the sibling path, and `shell.showItemInFolder(resolved)` opens/reveals `~/Documents/GitHub/project-secrets/config.json` in the OS file explorer — a file entirely outside the intended repository root.

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
