Since no further tool calls are available, here is my analysis based on the evidence gathered.

### Title
Unconfirmed automatic checkout of attacker-controlled pull request via `x-github-client://openRepo` deep link - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
The original report's broken invariant is: *a state-changing operation (burning shares) is performed on behalf of a party without checking that the caller is authorized/consented*. The closest reachable analog in GitHub Desktop is the `open-repository-from-url` deep-link handler, which — when the URL carries a `pr` query parameter — automatically fetches PR metadata from the GitHub API and checks out the PR author's fork/branch into the user's *existing, already-opened* local repository, with no confirmation dialog and no verification that the triggering link or the PR came from a trusted source.

### Finding Description
`parseAppURL` in `app/src/lib/parse-app-url.ts` (lines 66-128) parses any `x-github-client://openRepo/...` URL into an `open-repository-from-url` action, validating only that `pr` is numeric and `branch` matches `pr/\d+`. It performs no check that the URL, the PR number, or the repository was expected/consented to by the user. [1](#0-0) 

This action is dispatched from `dispatchURLAction`, invoked whenever the OS routes an `open-url` event to Desktop (i.e., any link the user clicks anywhere — browser, chat client, email) that uses the registered custom protocol: `handleAppURL` in `app/src/main-process/main.ts` calls `parseAppURL` and forwards the action to the renderer without any origin check. [2](#0-1) 

In the renderer, `dispatchURLAction` routes a `pr`-bearing action to `openPullRequestFromUrl`: [3](#0-2) 

`openPullRequestFromUrl` fetches the PR object from the GitHub API using attacker-chosen `url`/`pr` values, tries to match it against a repository the user already has open via `getRepositoryFromPullRequest`/`doesRepositoryMatchUrl` (URL string comparison only, no ownership/consent check), and if a match is found it calls `selectRepository` followed directly by `_checkoutPullRequest` with the PR's `head.repo.clone_url`, `owner.login`, and `head.ref` — all values fully controlled by whoever opened the PR: [4](#0-3) 

There is no popup, confirmation, or "trust this repository/branch" prompt in this path (unlike, for example, `MissingRepository`'s explicit "Trust Repository" gate for unsafe directories [5](#0-4) ). The only guard present anywhere nearby is the `filepath` traversal check using `resolveWithin`/`isAbsolute`, which protects a *different* sub-feature (opening a file) and does nothing to gate the checkout itself. [6](#0-5) 

### Impact Explanation
Anyone who can open a pull request against a public repository the victim already has cloned in Desktop (i.e., essentially any GitHub user), and get the victim to click one crafted link (`x-github-client://openRepo/<repo-url>?pr=<attacker-PR-number>`), can force Desktop to silently switch that existing local working directory to the attacker's fork/branch content without any confirmation. This is a "silent corruption of what the user commits or pushes" scenario: subsequent builds/tests/commits/pushes by the victim operate on attacker-supplied code, and if the victim builds/runs the project afterward this becomes a code-execution vector. The GitHub API object (the PR) and the deep link are both attacker-controlled, matching the report's "burn shares belonging to someone else without consent" pattern — here, "checkout/overwrite a repository belonging to someone else's workflow without consent."

### Likelihood Explanation
Requires only: (1) the victim has previously cloned the target repository in Desktop, (2) the attacker opens a PR against that repository (or any repository whose URL the attacker names), and (3) the victim clicks a link — a normal, unprivileged action reachable from a webpage, chat message, or issue/PR comment containing the custom-protocol URL. No local access, no leaked credentials, no malware needed.

### Recommendation
Before calling `_checkoutPullRequest` in `openPullRequestFromUrl` (and before `openBranchNameFromUrl`'s equivalent checkout), require explicit user confirmation showing the source repository/branch/owner, similar to the existing "Trust Repository" gate. Additionally, `dispatchURLAction`/`handleAppURL` should distinguish and rate-limit or confirm actions that mutate an *already open* repository's working tree, since those carry higher risk than opening a brand-new clone dialog (which already requires the user to explicitly confirm in the Clone dialog).

### Proof of Concept
1. Victim has `https://github.com/victim-org/victim-repo` open in GitHub Desktop.
2. Attacker opens a PR (`#1234`) against that repo from their own fork/branch.
3. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/victim-org/victim-repo?pr=1234`.
4. Victim clicks the link (e.g., in a browser). The OS invokes Desktop's `open-url` handler → `handleAppURL` → `parseAppURL` → `open-repository-from-url` action with `pr=1234`. [7](#0-6) 
5. `dispatchURLAction` → `openRepositoryFromUrl` → `openPullRequestFromUrl` matches the already-open `victim-repo`, fetches PR #1234 from the API, and calls `_checkoutPullRequest` with the attacker's fork URL and branch — checking out attacker content into the victim's existing working directory with no prompt. [8](#0-7)

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-116)
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
```

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
```

**File:** app/src/main-process/main.ts (L204-210)
```typescript
app.on('will-finish-launching', () => {
  // macOS only
  app.on('open-url', (event, url) => {
    event.preventDefault()
    handleAppURL(url)
  })
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

**File:** app/src/ui/missing-repository.tsx (L91-103)
```typescript
    } else {
      buttons.push(
        <Button
          key="trustDirectory"
          onClick={this.onTrustDirectory}
          type="submit"
          disabled={this.state.isTrustingPath}
        >
          {this.state.isTrustingPath && <Loading />}
          {__DARWIN__ ? 'Trust Repository' : 'Trust repository'}
        </Button>
      )
    }
```
