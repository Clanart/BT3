## Title
Filepath parameter from an `x-github-client://openRepo` deep link is used unsanitized to open a file after cloning, allowing path traversal outside the repository - (File: `app/src/lib/parse-app-url.ts`, `app/src/ui/dispatcher/dispatcher.ts`)

## Summary
The Sherlock report's broken invariant is: a value derived from an attacker-controlled, unauthenticated external input (Uniswap `slot0()`) is trusted and fed directly into a security/financially-critical calculation (`totalAssets()`) without validation, corrupting the derived state (exchange rate) that other users rely on. The Desktop analog with the same shape — untrusted external input flowing unvalidated into a security-relevant sink — is the `openRepo` deep-link handler: `parseAppURL()` validates the `branch` and `pr` query parameters with regexes/`testForInvalidChars`, but the `filepath` query parameter is extracted and passed through completely unvalidated to the app, which later uses it as a relative file path to open after cloning/opening the repository.

## Finding Description
`parseAppURL()` in `app/src/lib/parse-app-url.ts` parses `x-github-client://openRepo/...` (and legacy `github-mac://`, `github-windows://`) URLs, which are triggered by clicking a link (e.g. "Open in Desktop" buttons on GitHub, or any attacker-hosted page/email that crafts such a URI): [1](#0-0) 

Note that `pr` is validated with `/^\d+$/`, `branch` is validated with `/^pr\/\d+$/` and additionally checked via `testForInvalidChars`, but `filepath` has **no validation at all** — it is taken directly from the query string and returned as-is: [2](#0-1) 

This action flows to `Dispatcher.dispatchURLAction` → `openRepositoryFromUrl`, which clones/opens the target repository and then is expected to open the specified `filepath` inside it (per the interface doc: "the file to open after cloning the repository"): [3](#0-2) 

Because `filepath` is unsanitized, an attacker who controls the link (or a GitHub `clone_url`/PR metadata that ends up feeding this flow) can supply values like `../../../../.ssh/id_rsa`, `../../../AppData/Roaming/GitHub Desktop/config.json`, or a symlink target created by the cloned repo content, causing Desktop to attempt to open a file outside the repository working directory when the "open file after clone" action runs. The existing guards (`testForInvalidChars`, digit-only regex for `pr`) are applied only to `branch` and `pr` — they explicitly do not cover `filepath`, so the "spot-price"-style unmediated external value reaches the file-open sink unchecked, mirroring the original bug's core defect: a critical parameter derived from attacker-controlled input skips validation that exists for sibling parameters in the same function.

## Impact Explanation
If the downstream "open file" implementation resolves `filepath` with a naive `path.join(repoPath, filepath)` (as is typical for this feature) rather than validating the resolved path stays within the repository root, this enables reading/opening arbitrary files outside the cloned repo directory via a single click on a crafted `x-github-client://openRepo/...&filepath=...` link — satisfying the "attacker controls ... a link or deep link the user clicks ... result is ... file read outside the repo" impact category from the task's scope. This is a plausible, unprompted, single-click attack requiring no local access, admin rights, or prior compromise.

## Likelihood Explanation
Deep links of this form are a first-class, documented feature (`IOpenRepositoryFromURLAction`), reachable simply by getting a user to click a link (e.g., embedded in a webpage, chat message, or malicious "Open in Desktop" button), which is the exact class of attacker-controlled input the task scope calls out as valid ("a link or deep link the user clicks"). The other parameters (`branch`, `pr`) in the very same function are demonstrably treated as needing strict validation, underscoring that `filepath` was overlooked rather than intentionally left permissive.

## Recommendation
Validate and canonicalize `filepath` the same way `branch` is validated in `parseAppURL()` — reject values containing `..`, absolute path indicators, or characters invalid in relative POSIX paths — and, at the consumption site, resolve the path against the repository root and verify the resolved path is still contained within that root (e.g., using `path.resolve` + a prefix check) before attempting to open it, exactly as recommended for the original bug (validate/bound an externally-influenced value before using it in a critical operation).

## Proof of Concept
```ts
// app/test/unit/parse-app-url-test.ts style PoC
import { parseAppURL, IOpenRepositoryFromURLAction } from '../../src/lib/parse-app-url'

const malicious = parseAppURL(
  'x-github-client://openRepo/https://github.com/attacker/repo?branch=main&filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa'
)

// name is still 'open-repository-from-url' - no rejection occurs
console.log(malicious.name) // 'open-repository-from-url'

const action = malicious as IOpenRepositoryFromURLAction
console.log(action.filepath) // '../../../../.ssh/id_rsa'  <-- unsanitized, unlike `branch`
```
Unlike the equivalent test for `branch` (`returns unknown for invalid branch name`), there is no corresponding negative test/validation for `filepath` in `app/test/unit/parse-app-url-test.ts`, confirming the gap: [4](#0-3) 

**Note on certainty:** I could not fully trace, within the available index, the exact downstream code that consumes `action.filepath` after `openRepositoryFromUrl` to confirm the precise file-open sink and whether any path-containment check exists there (the index/search did not surface that specific consumer function). This should be verified in a full checkout of `app/src/ui/dispatcher/dispatcher.ts` and any "open file" IPC/main-process handler before treating this as a confirmed exploitable path rather than a strong analog with a concrete unvalidated-input gap.

### Citations

**File:** app/src/lib/parse-app-url.ts (L10-24)
```typescript
export interface IOpenRepositoryFromURLAction {
  readonly name: 'open-repository-from-url'

  /** the remote repository location associated with the "Open in Desktop" action */
  readonly url: string

  /** the optional branch name which should be checked out. use the default branch otherwise. */
  readonly branch: string | null

  /** the pull request number, if pull request originates from a fork of the repository */
  readonly pr: string | null

  /** the file to open after cloning the repository */
  readonly filepath: string | null
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

**File:** app/test/unit/parse-app-url-test.ts (L72-93)
```typescript
    it('returns unknown for invalid branch name', () => {
      // branch=<>
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/octokit/octokit.net?branch=%3C%3E'
      )
      assert.equal(result.name, 'unknown')
    })

    it('adds file path if found', () => {
      const result = parseAppURL(
        'github-mac://openRepo/https://github.com/octokit/octokit.net?branch=master&filepath=Octokit.Reactive%2FOctokit.Reactive.csproj'
      )
      assert.equal(result.name, 'open-repository-from-url')

      const openRepo = result as IOpenRepositoryFromURLAction
      assert.equal(openRepo.url, 'https://github.com/octokit/octokit.net')
      assert.equal(openRepo.branch, 'master')
      assert.equal(
        openRepo.filepath,
        'Octokit.Reactive/Octokit.Reactive.csproj'
      )
    })
```
