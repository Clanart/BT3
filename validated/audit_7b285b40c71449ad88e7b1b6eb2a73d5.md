Found a concrete, code-supported analog in `openPullRequestFromUrl`: the PR object used to decide *what to check out* is fetched fresh from the API by number, but the deep-link/URL that triggered the flow is fully attacker-controlled, and the branch/repo that ends up checked out is whatever the PR's `head` looks like *at the moment of resolution*, not necessarily what the user believed they were opening. This mirrors the report's core invariant: the object that grants "ownership"/trust (NFT ⇔ deep link) can point to a materially different underlying state (vault content ⇔ checkout target) at the moment the action executes than what the user was shown or expects.

### Title
Deep-link "Open Pull Request" flow checks out an attacker-controlled fork/branch resolved at click-time with no origin confirmation - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
`x-github-client://openRepo/<url>?pr=<n>` deep links are parsed by `parseAppURL` [1](#0-0)  and dispatched to `openRepositoryFromUrl`, which for PR links calls `openPullRequestFromUrl(url, pr)` [2](#0-1) . That function fetches the PR by number from the API and then silently determines the head repo/branch to check out from whatever the API returns *at that instant*, without ever showing the user the resolved head repository/owner/branch before acting.

### Finding Description
`openPullRequestFromUrl` resolves the pull request via `this.appStore.fetchPullRequest(url, pr)`, and — critically — uses the **freshly fetched** `pullRequest.head.repo.clone_url` / `pullRequest.head.ref` to drive `_checkoutPullRequest`, not any value the user reviewed: [3](#0-2) . The `pr` number and base `url` are the only attacker-supplied invariants (validated only as a plain integer by `parseAppURL` [4](#0-3) ); the actual fork owner, clone URL, and ref are entirely determined by the *current* state of that PR on GitHub, which the PR author (or anyone with push access to the PR head) can change at any time — including after crafting/sharing the link and before the victim clicks it, or by racing the click itself.

This reproduces the report's exact broken invariant: the token/identifier presented to the user (NFT / PR-number link) is decoupled from the mutable underlying state (vault collateral / PR head), and the system acts on whatever that state is *at exchange time* rather than what was implied when the link was created or clicked. There is no confirmation step showing "you are about to check out branch X from repository Y" before `_checkoutPullRequest` runs — it happens automatically as part of handling the URL action [5](#0-4) .

`_checkoutPullRequest` then resolves and checks out the branch without any additional trust boundary re-check: [6](#0-5) .

### Impact Explanation
An attacker who controls (or can influence, e.g. by having write access to) a PR head, or who simply times the interaction, can cause a user who clicks a seemingly benign `x-github-client://openRepo/...?pr=N` link to have Desktop silently add a remote for and check out an arbitrary fork/branch the attacker controls, with the working directory of a real, possibly existing local repository being switched to attacker-authored code without an explicit "this comes from `owner/fork`" confirmation. Combined with any build/run tooling a developer might subsequently execute, this is a path to executing attacker-supplied code, matching "attacker controls a git remote/... link a user clicks" → code execution / silent corruption of what the user actually gets, per the report's theme of "buyer receives something in a different state than expected."

### Likelihood Explanation
Deep links of this form (`x-github-client://openRepo/...`) are the documented, supported way GitHub.com's "Open with GitHub Desktop" buttons work, so the attack surface is real and reachable by any external web page or malicious PR "Open in Desktop" button embedding the URL scheme; the only user action required is a single click, which is within the accepted threat model (attacker-controlled link a user clicks).

### Recommendation
Before invoking `_checkoutPullRequest`, surface a confirmation to the user showing the resolved head repository owner/clone URL and branch name that will actually be fetched and checked out, and re-validate that the resolved head repository is the one implied by the deep link's `url` parameter (or explicitly warn when it differs, e.g. fork vs. base repo mismatch). Consider pinning/validating against the `url` argument already parsed from the link rather than trusting the API response unconditionally for a security-relevant action such as checkout.

### Proof of Concept
1. Attacker opens a PR (or gets push access to an existing PR's head branch) against any repository the target already has open in Desktop, and shares a link such as `x-github-client://openRepo/https://github.com/victim-org/victim-repo?pr=123`.
2. Victim clicks the link. `parseAppURL` validates only that `pr` is numeric [4](#0-3) , and `dispatchURLAction` routes to `openRepositoryFromUrl` → `openPullRequestFromUrl` [2](#0-1) .
3. Before the click is processed, the attacker (re)points PR #123's head to a malicious fork/branch via the GitHub API/UI.
4. `openPullRequestFromUrl` fetches PR #123 fresh, and unconditionally checks out `pullRequest.head.repo.clone_url` / `pullRequest.head.ref` [7](#0-6)  — the victim's working directory now reflects attacker-controlled content with no prompt showing what was checked out or from where.

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

**File:** app/src/lib/stores/app-store.ts (L8613-8631)
```typescript
  public async _checkoutPullRequest(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<void> {
    const prBranch = await this._findPullRequestBranch(
      repository,
      prNumber,
      headRepoOwner,
      headCloneUrl,
      headRefName
    )
    if (prBranch !== undefined) {
      await this._checkoutBranch(repository, prBranch)
      this.statsStore.increment('prBranchCheckouts')
    }
  }
```
