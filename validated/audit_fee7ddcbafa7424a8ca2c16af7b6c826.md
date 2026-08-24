## Title
Deep-link PR checkout trusts a re-resolved fork URL/ref instead of the SHA it displayed, allowing a bait-and-switch code substitution - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
GitHub Desktop's `x-github-client://openRepo/...?pr=N` deep link handler fetches pull-request metadata once (time-of-check), then performs several more network round-trips before finally checking out a branch built from the *same mutable identifiers* (`clone_url` + `ref`, not the SHA that was fetched) rather than the specific commit that was originally resolved. Because the "head" of a PR is an attacker-controlled fork, this is structurally the same class of bug as the reported Uniswap TOCTOU: an identifier (tab ID / here, PR number+clone URL+branch name) is captured at check time and re-resolved via a fresh, independent lookup at use time, with no binding to the state that was actually inspected/authorized.

### Finding Description
`openPullRequestFromUrl` in `dispatcher.ts` implements the "Open in Desktop" / PR deep-link flow: [1](#0-0) 

Step by step:
1. `fetchPullRequest(url, pr)` performs a live GitHub API call and returns `pullRequest.head.repo.clone_url`, `pullRequest.head.ref`, and `pullRequest.head.sha` for whatever the fork's branch currently points to. [2](#0-1) 
2. The function then does `getRepositoryFromPullRequest`, possibly `selectRepository`/`openOrCloneRepository`, and `_refreshRepository` — each an async step with its own event-loop turns and possibly its own network calls. [3](#0-2) 
3. Finally it calls `_checkoutPullRequest(repository, pullRequest.number, head.owner.login, head.clone_url, head.ref)` — passing the *ref name and clone URL*, not the SHA fetched in step 1.
4. `_checkoutPullRequest` → `_findPullRequestBranch` adds/uses a fork remote and does a **fresh** `_fetchRemote` against that same clone URL, then checks out whatever `remote.name/headRefName` currently resolves to: [4](#0-3) 

At no point is `pullRequest.head.sha` (captured in step 1) compared against what is actually fetched/checked out in step 4. Because the head repository is a fork that the PR author (or anyone with push access to it) fully controls, the branch can be force-pushed to point at different content at any time between step 1 and step 4 — including in the seconds while Desktop is still fetching/cloning/refreshing the base repository. The deep link itself (`x-github-client://openRepo/<url>?pr=<n>`) is validated only for well-formedness, not tied to any commit: [5](#0-4) 

This mirrors the report's structure precisely: a "check" (fetch metadata, e.g. `chrome.tabs.get`) is done once, then a semantically identical but independently re-resolved "use" (fetch again / check out again) is performed later using the same mutable identifier (tab ID ↔ clone URL+ref), and nothing pins the second lookup to the state observed by the first.

### Impact Explanation
An attacker who controls a fork referenced by a PR (which is any GitHub user, since PRs can be opened from arbitrary forks) can:
- Publish a PR link/deep-link (e.g. embedded in a webpage's "Open in Desktop" button, which any site can construct) pointing at seemingly benign code.
- Force-push malicious content to the head branch immediately before/while the victim's Desktop client processes the multi-step async flow above.
- Have Desktop silently check out the malicious commit into the victim's working directory under the same branch name/PR number the victim believed they were opening, with no SHA verification or warning that the content changed since it was resolved.

This satisfies the "silent corruption of what the user commits/pushes" impact bucket: the victim may review, build, run, or commit on top of code that differs from what was actually vetted/displayed, and if the victim later pushes on top of it, malicious commits get pushed under the victim's identity. The attack requires only clicking a link/deep-link the attacker controls — no local access, malware, or leaked credentials.

### Likelihood Explanation
Moderate. The attacker needs the victim to click a `x-github-client://` link and needs to time a force-push to the fork's head ref to land within the multi-await window between `fetchPullRequest` and the final `_fetchRemote`/checkout in `_findPullRequestBranch`. This window includes real network I/O (`_refreshRepository`, potential clone, `_fetchRemote`), which is on the order of seconds and can be extended arbitrarily by the attacker's own server (e.g., delaying the initial page load or clone traffic, similar to the PoC in the original report that inserted a `setTimeout`/navigation before firing the request). No user privilege or additional interaction beyond the initial click is required.

### Recommendation
- Thread `pullRequest.head.sha` through `_checkoutPullRequest`/`_findPullRequestBranch` and verify, after fetching the fork remote, that the resolved ref's SHA matches the SHA originally shown/fetched; if it doesn't, warn the user (similar in spirit to the report's "use the URL as the only authentication data" recommendation — here, "pin to the commit SHA, not the mutable ref+URL pair").
- If the SHA differs, either check out the specific SHA directly (`git checkout <sha>`) into a detached/pr branch rather than the ref name, or surface a confirmation dialog stating the PR's content changed since it was opened.
- Apply the same SHA-pinning to `openBranchNameFromUrl`/`dispatchCLIAction`'s clone-url+branch flow, which has the analogous issue for non-PR deep links.

### Proof of Concept
1. Attacker opens PR #123 from `fork` to `victim/repo` with benign-looking code, and gets a link containing this PR onto a page the victim will click (e.g. an "Open in Desktop" button, or a raw `x-github-client://openRepo/https://github.com/victim/repo?pr=123&branch=pr/123` link).
2. Victim clicks the link. Desktop's `openPullRequestFromUrl` calls `fetchPullRequest` and receives `head.sha = A` (benign commit).
3. While Desktop performs `getRepositoryFromPullRequest` → `selectRepository`/clone → `_refreshRepository` (several seconds, extendable by attacker-controlled network delay on `fork`'s git server), attacker force-pushes fork's branch so it now points to malicious commit `B`.
4. Desktop calls `_checkoutPullRequest(..., headCloneUrl, headRefName)`; `_findPullRequestBranch` fetches the fork remote fresh and checks out whatever the ref now resolves to — commit `B` — with no comparison to `A`.
5. Victim's working directory now silently contains malicious commit `B` under branch `pr/123`, believed to be PR #123's reviewed content.

Note: I was not able to execute this flow to confirm timing feasibility in a live client — this analysis is based on static code review of the cited files; a background Devin session with terminal/browser access could reproduce and time the actual race window.

### Citations

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

**File:** app/src/lib/stores/app-store.ts (L2337-2349)
```typescript
  public async fetchPullRequest(repoUrl: string, pr: string) {
    const endpoint = getEndpointForRepository(repoUrl)
    const account = getAccountForEndpoint(this.accounts, endpoint)

    if (account) {
      const api = API.fromAccount(account)
      const remoteUrl = parseRemote(repoUrl)
      if (remoteUrl && remoteUrl.owner && remoteUrl.name) {
        return await api.fetchPullRequest(remoteUrl.owner, remoteUrl.name, pr)
      }
    }
    return null
  }
```

**File:** app/src/lib/stores/app-store.ts (L8633-8721)
```typescript
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
