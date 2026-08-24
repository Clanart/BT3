Based on my investigation, I found a genuine analog in GitHub Desktop's deep-link "Open PR from Desktop" flow, which mirrors the exact broken invariant from the report: an identity check is performed against a *reusable string address* (owner/name/hostname) rather than a *stable, unique identifier*, allowing two different underlying resources to be treated as identical.

### Title
PR-to-local-repository matching relies on non-unique clone URL strings instead of stable repository identity - ([File: app/src/ui/dispatcher/dispatcher.ts])

### Summary
`Dispatcher.getRepositoryFromPullRequest` selects which locally-tracked repository a deep-linked pull request should be opened against by comparing the PR's `head`/`base` `clone_url` strings to the `htmlURL`/`parent.htmlURL` of each locally known `GitHubRepository`, using `urlsMatch`, which itself only compares `hostname`/`owner`/`name` substrings parsed out of the URL. [1](#0-0) [2](#0-1) 

### Finding Description
`urlsMatch` parses two URLs and returns true purely if `hostname`, `owner`, and `name` are equal — there is no comparison against GitHub's stable numeric repository id, database id, or any other non-reassignable identifier. [2](#0-1) 

This is the same class of bug as the VTVL finding: the code assumes a 1:1, permanent mapping between an "address" (here, `owner/name` on a host) and a specific underlying resource (a specific GitHub repository), and gates a sensitive action — selecting which local, already-cloned repository will have `dispatcher._checkoutPullRequest` invoked against it — using only that address, never a durable identity check. On GitHub, an `owner/name` slug is not permanently bound to one repository: repositories can be deleted, renamed away from, or an org/user can be renamed/deleted and the name later reused, after which `owner/name` (and thus `clone_url`) will again resolve to a *different* repository while remaining textually identical to what's stored in Desktop's persisted `GitHubRepository.htmlURL`.

The consuming call path is:
`openRepositoryFromUrl` → `openPullRequestFromUrl` → `getRepositoryFromPullRequest` (string match) → if matched, `selectRepository` + `_checkoutPullRequest` on the *existing local clone*, with no re-validation that the PR's head repository is actually the same repository the local clone was cloned from. [3](#0-2) [4](#0-3) 

### Impact Explanation
If an attacker (or a legitimate but no-longer-controlled `owner/name`) causes the `clone_url` recorded for a PR's `head`/`base` repo to textually match a URL a victim already has tracked locally (e.g., because the original repository was deleted/renamed and the slug was later claimed by someone else), a crafted `x-github-desktop://` "Open PR" deep link can cause Desktop to silently `fetch` and `_checkoutPullRequest` a completely different, attacker-controlled repository's branch into the victim's existing, trusted local working copy. This is a silent-corruption path: the user did not choose to fetch or check out attacker content, yet their working directory (and potentially their subsequent commits built on that checkout) now contains attacker-supplied code, satisfying the "silent corruption of what the user commits or pushes" impact class.

### Likelihood Explanation
This requires an external precondition (name/ownership churn on GitHub's side) analogous to the "rare token implementation" caveat the original report's judge used to downgrade severity — it is not attacker-controlled in the general case, only in the specific case where the victim's tracked repository's owner/name has since become available and was reclaimed. Because of this precondition, likelihood is low-to-medium, matching the original finding's own acknowledged rarity/medium rating rather than being trivially triggerable by any attacker at will.

### Recommendation
Replace/augment the string-based `urlsMatch` comparison with a check against GitHub's stable repository id (`GitHubRepository.dbId`/`ghAPIId` if tracked, or a freshly-fetched API id comparison) before selecting an existing local repository to operate on for a deep-linked PR, analogous to the balance-check mitigation recommended in the original report (verify identity via an unspoofable property, not a reusable address).

### Proof of Concept
1. Victim clones `https://github.com/acme/widgets` in Desktop; it is tracked with `GitHubRepository.htmlURL = "https://github.com/acme/widgets"`.
2. The `acme` org is later renamed/deleted and a different, unrelated `acme` account is created owning a different `widgets` repository (or the same repo is deleted and recreated by a new owner claiming the freed slug).
3. An attacker sends the victim an `x-github-desktop://openRepo/...` deep link referencing a PR whose `head.repo.clone_url` is `https://github.com/acme/widgets`.
4. `getRepositoryFromPullRequest` → `doesRepositoryMatchUrl` → `urlsMatch` matches purely on hostname/owner/name string equality and returns the victim's original, unrelated local `widgets` clone. [5](#0-4) 
5. `_checkoutPullRequest` fetches and checks out the attacker's branch into the victim's pre-existing local repository without any additional identity confirmation. [6](#0-5) 

Note: I was unable to fully verify, within available tool budget, whether Electron's `webRequest.onBeforeSendHeaders` re-evaluates origin on every redirect hop for the related `authenticated-image-filter.ts` Authorization-header-attachment logic I also examined; that path looked superficially similar (origin/regex-based matching rather than exact resource identity) but I could not confirm an actual cross-origin credential leak, so I have not included it as a confirmed finding.

### Citations

**File:** app/src/ui/dispatcher/dispatcher.ts (L1890-1938)
```typescript
  private getRepositoryFromPullRequest(
    pullRequest: IAPIPullRequest
  ): RepositoryWithGitHubRepository | null {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const headUrl = pullRequest.head.repo?.clone_url
    const baseUrl = pullRequest.base.repo?.clone_url

    // This likely means that the base repository has been deleted
    // and we don't support checking out from refs/pulls/NNN/head
    // yet so we'll bail for now.
    if (headUrl === undefined || baseUrl === undefined) {
      return null
    }

    for (const repository of repositories) {
      if (this.doesRepositoryMatchUrl(repository, headUrl)) {
        return repository
      }
    }

    for (const repository of repositories) {
      if (this.doesRepositoryMatchUrl(repository, baseUrl)) {
        return repository
      }
    }

    return null
  }

  private doesRepositoryMatchUrl(
    repo: Repository | CloningRepository,
    url: string
  ): repo is RepositoryWithGitHubRepository {
    if (repo instanceof Repository && isRepositoryWithGitHubRepository(repo)) {
      const originRepoUrl = repo.gitHubRepository.htmlURL
      const upstreamRepoUrl = repo.gitHubRepository.parent?.htmlURL ?? null

      if (originRepoUrl !== null && urlsMatch(originRepoUrl, url)) {
        return true
      }

      if (upstreamRepoUrl !== null && urlsMatch(upstreamRepoUrl, url)) {
        return true
      }
    }

    return false
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1955)
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

**File:** app/src/lib/repository-matching.ts (L137-148)
```typescript
export function urlsMatch(url1: string, url2: string) {
  const firstIdentifier = parseRepositoryIdentifier(url1)
  const secondIdentifier = parseRepositoryIdentifier(url2)

  return (
    firstIdentifier !== null &&
    secondIdentifier !== null &&
    firstIdentifier.hostname === secondIdentifier.hostname &&
    firstIdentifier.owner === secondIdentifier.owner &&
    firstIdentifier.name === secondIdentifier.name
  )
}
```
