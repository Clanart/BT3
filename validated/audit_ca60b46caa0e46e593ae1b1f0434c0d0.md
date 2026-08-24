## Title
Malicious "Open Pull Request in Desktop" deep link silently checks out attacker-controlled fork code into an already-cloned local repository - (File: app/src/ui/dispatcher/dispatcher.ts)

### Summary
The external report's broken invariant is: an attacker can inject an ID/parameter they control into a flow that a victim believes is under their own control, redirecting the *result* of the victim's action toward attacker-supplied state, with no confirmation step to catch the substitution. In GitHub Desktop the analogous primitive is the `x-github-client://openRepo/...?pr=<n>` deep link. Anything the attacker can put in a link the user clicks — the repository URL and PR number — is used to look up an existing, already-trusted local repository and then silently perform a `git fetch`/`checkout` of the attacker's fork branch into it, without any confirmation dialog.

### Finding Description
`parseAppURL` accepts an attacker-controlled `url`, `pr`, and `branch` from any `openRepo` deep link with only superficial validation (digits-only `pr`, ref-char check on `branch`) [1](#0-0) .

`dispatchURLAction` routes `open-repository-from-url` actions straight into `openRepositoryFromUrl` with no additional gating [2](#0-1) .

When a `pr` parameter is present, `openPullRequestFromUrl` fetches the PR object from the GitHub API using the attacker-supplied `url`/`pr` [3](#0-2) , then calls `getRepositoryFromPullRequest`, which matches the PR's `head`/`base` `clone_url` (both attributes of the API object, and thus attacker-influenced since the attacker files the PR) against the origin/upstream remote of repositories the victim already has open in Desktop [4](#0-3) [5](#0-4) .

If a match is found — which is trivial, since the attacker only needs to pick a `url` for a popular/legitimate project the victim already has cloned — Desktop selects that existing repository and unconditionally calls `_checkoutPullRequest` with the attacker's fork `clone_url` and `head.ref` [6](#0-5) . `_checkoutPullRequest`/`_findPullRequestBranch` then adds (or reuses) a remote pointing at the attacker's fork, fetches it, creates a local `pr/<n>` branch, and checks it out — all silently, with no confirmation dialog shown to the user [7](#0-6) .

Existing guards do not stop this path: `urlMatchesRemote`/`urlsMatch` only validate hostname/owner/name equality of URLs, not the trustworthiness of the PR's head fork [8](#0-7) ; there is no check that the deep link originated from an interactive, user-initiated "Open in Desktop" click on github.com versus an arbitrary external link, and no confirmation prompt equivalent to the one used elsewhere for stash/branch conflicts.

### Impact Explanation
This corresponds to "silent corruption of what the user commits or pushes." After the link is clicked, the victim's working directory in an existing, trusted repository is silently switched to a branch created from an attacker-controlled fork. If the victim is unaware of the switch and continues to work, review, commit, or push, they may commit on top of, or push, attacker-supplied history/code under the wrong pretense, or unknowingly review/build/run attacker-modified code they believe belongs to the legitimate upstream project.

### Likelihood Explanation
The attack requires only a single click on a crafted `x-github-client://openRepo/<repo-url>?pr=<n>` link (e.g., in an email, chat message, or malicious webpage) — no local access, no malware, and no leaked credentials, matching the accepted "link/deep link the user clicks" and "GitHub API object" attacker-control categories. The only precondition is that the victim already has the target project cloned in Desktop and has an account authorized against that endpoint, which is the common case for anyone likely to be targeted via a PR-related link for that project.

### Recommendation
Before executing `_checkoutPullRequest` when it originates from a URL-handler/CLI action, show an explicit confirmation dialog that names the target local repository, the PR number, the fork owner, and the fork clone URL, and require the user to confirm before adding a remote/fetching/checking out. Treat `open-repository-from-url` PR checkouts the same as any other "fetch and checkout of remote content initiated by an untrusted external URL" and require the confirmation regardless of whether a matching existing repository is found.

### Proof of Concept
1. Attacker forks `github.com/<victim-project-owner>/<victim-project>` (a project the victim already has cloned in GitHub Desktop) and opens PR #N against it from the fork, with any branch/content.
2. Attacker sends the victim: `x-github-client://openRepo/https://github.com/<victim-project-owner>/<victim-project>?pr=<N>`.
3. Victim clicks the link. `parseAppURL` parses it into an `open-repository-from-url` action with `pr="<N>"` [1](#0-0) .
4. `dispatchURLAction` → `openRepositoryFromUrl` → `openPullRequestFromUrl` fetches PR #N via the API, matches `pullRequest.base.repo.clone_url` to the victim's already-open local repository, selects it, and calls `_checkoutPullRequest` with the attacker fork's `clone_url`/`head.ref` [9](#0-8) .
5. `_findPullRequestBranch` adds a `github-desktop-<attacker-owner>` remote, fetches it, creates and checks out branch `pr/<N>` [7](#0-6)  — with no confirmation shown to the victim, leaving their working directory populated with attacker-controlled code/history.

### Citations

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1890-1918)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1920-1938)
```typescript
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2047)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```

**File:** app/src/lib/stores/app-store.ts (L8613-8721)
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

  public async _findPullRequestBranch(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<Branch | undefined> {
    const gitStore = this.gitStoreCache.get(repository)
    const remotes = await getRemotes(repository)

    // Find an existing remote (regardless if set up by us or outside of
    // Desktop).
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
      }
    }

    const remoteRef = `${remote.name}/${headRefName}`

    // Start by trying to find a local branch that is tracking the remote ref.
    let existingBranch = gitStore.allBranches.find(
      x => x.type === BranchType.Local && x.upstream === remoteRef
    )

    // If we found one, let's check it out and get out of here, quick
    if (existingBranch !== undefined) {
      return existingBranch
    }

    const findRemoteBranch = (name: string) =>
      gitStore.allBranches.find(
        x => x.type === BranchType.Remote && x.name === name
      )

    // No such luck, let's see if we can at least find the remote branch then
    existingBranch = findRemoteBranch(remoteRef)

    // It's quite possible that the PR was created after our last fetch of the
    // remote so let's fetch it and then try again.
    if (existingBranch === undefined) {
      try {
        await this._fetchRemote(repository, remote, FetchType.UserInitiatedTask)
        existingBranch = findRemoteBranch(remoteRef)
      } catch (e) {
        log.error(`Failed fetching remote ${remote?.name}`, e)
      }
    }

    if (existingBranch === undefined) {
      this.emitError(
        new Error(
          `Couldn't find branch '${headRefName}' in remote '${remote.name}'. ` +
            `A common reason for this is that the PR author has deleted their ` +
            `branch or their forked repository.`
        )
      )
      return
    }

    // For fork remotes we checkout the ref as pr/[123] instead of using the
    // head ref name since many PRs from forks are created from their default
    // branch so we'll have a very high likelihood of a conflicting local branch
    const isForkRemote =
      remote.name !== gitStore.defaultRemote?.name &&
      remote.name !== gitStore.upstreamRemote?.name

    if (isForkRemote) {
      return await this._createBranch(
        repository,
        `pr/${prNumber}`,
        remoteRef,
        false
      )
    }

    return existingBranch
  }
```

**File:** app/src/lib/repository-matching.ts (L90-148)
```typescript
export function urlMatchesRemote(url: string | null, remote: IRemote): boolean {
  if (url == null) {
    return false
  }

  const cloneUrl = parseRemote(url)
  const remoteUrl = parseRemote(remote.url)

  if (remoteUrl == null || cloneUrl == null) {
    return false
  }

  if (!caseInsensitiveEquals(remoteUrl.hostname, cloneUrl.hostname)) {
    return false
  }

  if (remoteUrl.owner == null || cloneUrl.owner == null) {
    return false
  }

  if (remoteUrl.name == null || cloneUrl.name == null) {
    return false
  }

  return (
    caseInsensitiveEquals(remoteUrl.owner, cloneUrl.owner) &&
    caseInsensitiveEquals(remoteUrl.name, cloneUrl.name)
  )
}

/**
 * Match a URL-like string to the Clone URL of a GitHub Repository
 *
 * @param url A remote-like URL to verify against the existing information
 * @param gitHubRepository GitHub API details for a repository
 */
export function urlMatchesCloneURL(
  url: string,
  gitHubRepository: GitHubRepository
): boolean {
  if (gitHubRepository.cloneURL === null) {
    return false
  }

  return urlsMatch(gitHubRepository.cloneURL, url)
}

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
