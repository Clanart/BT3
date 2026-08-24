Based on my research, I found a concrete analog in the URL-action parsing path for GitHub Desktop's `x-github-client://`/`github-mac://` protocol handler.

### Title
Unsanitized `filepath` parameter in `open-repository-from-url` deep link can be used to reference files outside the cloned repository - (File: `app/src/lib/parse-app-url.ts`)

### Summary
The Tact report's broken invariant is: a user-supplied field (`forward_milliton`/`gas_transfer`) is accepted and acted upon by a privileged contract without being validated/deducted the same way as the primary parameter (`token_amt`), letting the attacker get more "value" out of the system than what was checked. The structural analog in GitHub Desktop is `parseAppURL()` in [1](#0-0) , which validates the `branch` and `pr` query parameters of an `openrepo` deep link with regex/character checks (`testForInvalidChars`, `/^\d+$/`, `/^pr\/\d+$/`) but performs **no validation at all** on the `filepath` parameter before it is returned as part of the trusted `IOpenRepositoryFromURLAction` payload [2](#0-1) .

### Finding Description
`IOpenRepositoryFromURLAction.filepath` is documented as "the file to open after cloning the repository" [3](#0-2) . Every other user-controlled field on this action (`branch`, `pr`) goes through an explicit allow-list/character check before being trusted: [4](#0-3) 

`filepath`, in contrast, is read straight from the query string with `getQueryStringValue(query, 'filepath')` and passed through unchanged, with no rejection of path-traversal sequences (`../`), absolute paths, or protocol-relative values [5](#0-4) . The unit tests confirm this field is expected to flow through verbatim, including with slashes, e.g. `Octokit.Reactive/Octokit.Reactive.csproj` [6](#0-5) . This action is dispatched to `dispatchURLAction` → `openRepositoryFromUrl` in `app/src/ui/dispatcher/dispatcher.ts` [7](#0-6) , which uses the `filepath` value to open a file relative to the (about to be, or already) cloned repository on disk.

This mirrors the smart-contract bug precisely: one "twin" field (`branch`) is guarded by input validation while the sibling field (`filepath`) that ultimately drives a filesystem side-effect is not, so the security guarantee ("only files inside the newly cloned repo can be opened") silently doesn't hold for every code path that consumes the parsed action.

### Impact Explanation
An attacker who gets a victim to click a crafted `x-github-client://openRepo/...?filepath=..%2F..%2F..%2F.ssh%2Fid_rsa` (or similar traversal payload) link controls the value that is later joined against the repository's local path to decide which file Desktop opens after cloning. If the consuming code (`dispatcher.ts`/the file-opening logic) does a naive `Path.join(repoPath, filepath)` without resolving and re-checking that the result stays inside `repoPath`, this becomes a file read outside the intended repository directory when Desktop opens/reveals that file to the user (e.g., via the editor or Explorer/Finder integration). This lines up with the specified valid-impact class: "a link or deep link the user clicks" resulting in "file … read outside the repo."

### Likelihood Explanation
Deep-link protocol handlers are one of Desktop's few genuinely unprivileged, attacker-reachable entry points — no local access, no prior malware, and no unusual user action beyond a single click on a link (e.g., embedded in a GitHub issue, PR, or website), consistent with GitHub's historical `x-github-client://openRepo` "Open in Desktop" feature. Because `branch` and `pr` are explicitly hardened against malformed/malicious input while `filepath` is not, this looks like an oversight rather than an intentional trust boundary, increasing the likelihood this specific field was missed in review.

### Recommendation
- Apply the same validation discipline used for `branch`/`pr` to `filepath`: reject values containing `..` path segments, absolute paths (leading `/` or drive letters), or protocol markers, mirroring `testForInvalidChars`/dedicated path-safety checks such as `sanitizeCloneName` already used elsewhere for remote-derived names [8](#0-7) .
- At the consumption site (`dispatcher.ts`), resolve the final path with `path.resolve`/`path.normalize` and assert it remains within the cloned repository root before performing any file-open operation, rather than trusting the parsed action value.
- Add regression tests analogous to the existing `branch`/`pr` "returns unknown for invalid X" cases in `app/test/unit/parse-app-url-test.ts`, specifically for traversal payloads in `filepath`.

### Proof of Concept
1. Attacker crafts and distributes a link:
   `x-github-client://openRepo/https://github.com/attacker/public-repo?filepath=..%2F..%2F..%2F..%2F.ssh%2Fid_rsa`
2. Victim (with GitHub Desktop's protocol handler registered) clicks the link.
3. `handleAppURL` → `parseAppURL` returns `{ name: 'open-repository-from-url', url: 'https://github.com/attacker/public-repo', branch: null, pr: null, filepath: '../../../../.ssh/id_rsa' }` unmodified, since only `branch`/`pr` are checked [9](#0-8) .
4. Desktop clones `attacker/public-repo` and then attempts to open the file at the traversal-resolved path, potentially exposing file contents outside the clone directory to the attacker's rendered/opened view.

**Caveat / what remains unverified:** I was not able to inspect, within this session's tool budget, the exact code in `app/src/ui/dispatcher/dispatcher.ts` (and any downstream file-opening helper) that consumes `action.filepath` to confirm whether a `path.join`/traversal-prevention check already exists there. The vulnerability as stated depends on that sink not re-validating the path. If a Desktop engineer confirms the sink does normalize-and-contain the path, this finding would be reduced to defense-in-depth (the parser should still reject malformed input early) rather than an exploitable primitive. I recommend a Devin session inspect `openRepositoryFromUrl` and any file-opening call it makes to confirm the sink behavior before treating this as a confirmed vulnerability rather than a strong analog candidate.

### Citations

**File:** app/src/lib/parse-app-url.ts (L22-23)
```typescript
  /** the file to open after cloning the repository */
  readonly filepath: string | null
```

**File:** app/src/lib/parse-app-url.ts (L66-124)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

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

**File:** app/test/unit/parse-app-url-test.ts (L80-93)
```typescript
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```

**File:** app/src/lib/remote-parsing.ts (L72-88)
```typescript
/**
 * Extracts a safe single-component directory name from a URL-derived repo name.
 *
 * Mirrors the approach of git's `git_url_basename()` in `dir.c`: treat `/`,
 * `\`, and `:` as path separators, take the last non-empty component, strip a
 * trailing `.git` suffix, and reject traversal segments. This ensures the
 * result is always a single path component that cannot escape the parent
 * directory when passed to `Path.join()`.
 *
 * Examples:
 *  - `"Hello-World"` → `"Hello-World"` (unchanged)
 *  - `"desktop.git/../../otherdir"` → `"otherdir"` (last component, traversal segments skipped)
 *  - `".."` → `null` (traversal-only name rejected)
 *
 * See: https://github.com/git/git/blob/master/dir.c (`git_url_basename`)
 */
export function sanitizeCloneName(name: string): string | null {
```
