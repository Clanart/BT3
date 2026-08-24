### Title
Deep-link `open-repository-from-url` action performs fetch/branch-checkout/PR-checkout with no signature, origin, or user-confirmation binding - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
The ChainPort report's core lesson is that a signed message needs context-binding fields (`networkId`, `action`) and dual uniqueness checks because a single loosely-specified credential (a signature) can be replayed across contexts it wasn't intended for. GitHub Desktop's `x-github-client://openrepo/...` (and legacy `github-mac://`/`github-windows://`) deep-link action is the analogous "credential-free" trust boundary: it is not signed or scoped in any way, yet it directly drives fetch/checkout operations against a repository the user already has cloned, based solely on attacker-controlled `branch`/`pr`/`filepath` URL parameters parsed by `parseAppURL`.

### Finding Description
`parseAppURL` [1](#0-0)  extracts `branch`, `pr`, and `filepath` straight from an OS-level custom-protocol URL with only syntactic validation (regex/format checks), no authentication, no signature, and no binding to the account or session that "owns" the repository. This is unlike the `oauth` action, which at least carries a `state` value checked against an in-flight session in `SignInStore.resolveOAuthRequest` [2](#0-1) .

Once parsed, `Dispatcher.dispatchURLAction` routes `open-repository-from-url` to `openRepositoryFromUrl` [3](#0-2) , which — if the repository already exists locally and its origin/upstream URL textually matches the link's `url` (`doesRepositoryMatchUrl`/`urlsMatch`) — silently performs a `fetch` and then a `checkout` of the attacker-specified branch or PR head, with **no popup, confirmation, or re-authentication**: [4](#0-3) [5](#0-4) 

The only defensive checks present are narrow, syntactic ones invented ad hoc for this feature (branch-name character validation, PR-number regex, and `resolveWithin`/absolute-path rejection for `filepath` [6](#0-5) ). There is no overarching specification describing what these deep-link actions are supposed to protect against (e.g., "must not silently change what's checked out in an existing local repo without confirmation", "must not be triggered by non-GitHub origins", "must not allow branch/PR values to be replayed against a repository the link's URL doesn't actually own"). Just as the ChainPort report noted that `networkId`/`action` fields and dual signature/nonce checks exist for reasons that are undocumented and easy to get wrong, Desktop's deep-link dispatcher has several ad hoc, uncoordinated guards (regex for `pr`, regex for `branch`, `testForInvalidChars`, `resolveWithin`) whose combined security property is never stated, making it easy for a future change to silently drop one of the checks (e.g., the `pr`/`branch` format constraints) without anyone realizing that removes the only protection against overwriting a developer's working directory with attacker-chosen content.

### Impact Explanation
If a user clicks such a link (from a phishing page, malicious README, chat message, etc.) while having the target repository already open in Desktop, the app will fetch and checkout a branch/PR chosen entirely by the attacker into the user's real working directory — this can silently overwrite uncommitted or staged files, put the user "on" a malicious branch, and set up subsequent unintended commits or pushes from that state (a "silent corruption of what the user commits/pushes" style impact). Combined with `filepath`, it also invokes `shell.showItemInFolder` on an attacker-influenced path within the repo (mitigated by `resolveWithin`, but that mitigation exists only because this one deep link happened to have it — nothing enforces this pattern elsewhere).

### Likelihood Explanation
Requires the victim to click a crafted link while Desktop is installed and the referenced repository already cloned/matched — no admin rights, no leaked credentials, and no unnatural steps beyond a normal "Open in Desktop"-style click, which is the exact interaction this protocol handler is designed to support. GitHub Desktop has a documented history of exactly this class of "Open in Desktop" deep-link issues (arbitrary path/branch checkout), which is why `resolveWithin`/regex checks were added piecemeal, reinforcing that the lack of a unifying specification is a real, recurring risk rather than a theoretical one.

### Recommendation
Write and enforce a specification for all `x-github-client://` (and legacy `github-mac`/`github-windows`) deep-link actions analogous to what the ChainPort report asks for signatures: state explicitly (1) what trust boundary each action crosses (arbitrary web content -> local git operations), (2) which parameters must be treated as fully untrusted, (3) why particular checks exist (format validation is not authentication), and (4) require explicit user confirmation before performing any mutating operation (fetch + checkout) on a pre-existing local repository triggered from a deep link, rather than silently performing it when the referenced repo happens to already exist.

### Proof of Concept
1. User has already cloned `https://github.com/victim-org/victim-repo` in Desktop.
2. Attacker hosts a page with `<a href="x-github-client://openrepo/https://github.com/victim-org/victim-repo?branch=pr/9999">Open in Desktop</a>` (or a `pr=` variant) and gets the user to click it (e.g. via a fake CI/PR-review notification).
3. `parseAppURL` accepts it as `open-repository-from-url` [7](#0-6) ; `Dispatcher.openRepositoryFromUrl` -> `openBranchNameFromUrl`/`openPullRequestFromUrl` matches the existing local repo and performs fetch + `checkoutLocalBranch`/`_checkoutPullRequest` with zero confirmation prompt [8](#0-7) .
4. The user's working directory is now checked out to attacker-chosen content without any dialog confirming this action took place.

Note: I could not fully trace `checkoutLocalBranch`'s implementation within the given tool budget (only its call sites were found, not its body), so I cannot confirm whether it independently discards uncommitted changes or prompts a stash dialog at that specific call site — this should be verified in a full Devin session before finalizing severity.

### Citations

**File:** app/src/lib/parse-app-url.ts (L66-128)
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
  }

  return unknown
}
```

**File:** app/src/lib/stores/sign-in-store.ts (L332-359)
```typescript
  public async resolveOAuthRequest(action: IOAuthAction) {
    if (!this.state || this.state.kind !== SignInStep.Authentication) {
      return
    }

    if (!this.state.oauthState) {
      return
    }

    if (this.state.oauthState.state !== action.state) {
      log.warn(
        'requestAuthenticatedUser was not called with valid OAuth state. This is likely due to a browser reloading the callback URL. Contact GitHub Support if you believe this is an error'
      )
      return
    }

    const { endpoint } = this.state
    const token = await requestOAuthToken(endpoint, action.code)

    if (token) {
      const account = await fetchUser(endpoint, token)
      this.state.oauthState.onAuthCompleted(account)
    } else {
      this.state.oauthState.onAuthError(
        new Error('Failed retrieving authenticated user')
      )
    }
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1996)
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
  }

  private async openBranchNameFromUrl(
    url: string,
    branchName: string
  ): Promise<Repository | null> {
    const repository = await this.openOrCloneRepository(url)

    if (repository === null) {
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    // if the repo has a remote, fetch before switching branches to ensure
    // the checkout will be successful. This operation could be a no-op.
    await this.appStore._fetch(repository, FetchType.UserInitiatedTask)

    await this.checkoutLocalBranch(repository, branchName)

    return repository
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2048)
```typescript
  private async openPullRequestFromUrl(
    url: string,
    pr: string
  ): Promise<RepositoryWithGitHubRepository | null> {
    const pullRequest = await this.appStore.fetchPullRequest(url, pr)

    if (pullRequest === null) {
      return null
    }

    // Find the repository where the PR is created in Desktop.
    let repository: Repository | null =
      this.getRepositoryFromPullRequest(pullRequest)

    if (repository !== null) {
      await this.selectRepository(repository)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      log.warn(
        `Open Repository from URL failed, did not find or clone repository: ${url}`
      )
      return null
    }
    if (!isRepositoryWithGitHubRepository(repository)) {
      log.warn(
        `Received a non-GitHub repository when opening repository from URL: ${url}`
      )
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    if (pullRequest.head.repo === null) {
      return null
    }

    await this.appStore._checkoutPullRequest(
      repository,
      pullRequest.number,
      pullRequest.head.repo.owner.login,
      pullRequest.head.repo.clone_url,
      pullRequest.head.ref
    )

    return repository
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```
