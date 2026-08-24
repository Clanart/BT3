## Title
Automatic, unconfirmed remote-URL rewrite lets a repository owner silently retarget where a contributor's commits are pushed - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The reported Sound Protocol bug is about a mutable, owner-controlled setting (`SAM`) that can be flipped on *after* users have already committed capital based on an initial, safer configuration, with no consent re-check from the affected users. The GitHub Desktop analog is `updateRemoteUrl()`, which silently rewrites a repository's `origin` remote URL based on data returned by the GitHub API, on every background refresh, with no dialog, no diff shown, and no user confirmation — even though this changes where the user's future `git push` commands actually send code.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository`, which fires automatically on repository selection, background fetch/indicator refresh, account-change refresh, and `_addRepositories`: [1](#0-0) 

The function itself performs the rewrite as long as (a) the protocol prefix matches, and (b) the *current* remote URL still equals the *previously cached* `gitHubRepository.cloneURL`: [2](#0-1) 

Because the "previously cached" `cloneURL` is itself updated on every refresh cycle (`repoStore.upsertGitHubRepository` / `setGitHubRepository` right after `updateRemoteUrl` runs), the `remoteUrlUnchanged` guard never actually locks the trust decision to what the user originally cloned — it only checks "did the URL change since the *last* automatic sync," so a repository owner can rename or transfer the tracked repository at any time (even long after the user cloned/committed to it) and Desktop will silently follow, indefinitely, with no user interaction: [3](#0-2) 

This differs sharply from the only other remote-URL-changing flow in the app that involves user-visible confirmation, `UpstreamAlreadyExists`, which explicitly asks the user before rewriting a remote: [4](#0-3) 

No equivalent confirmation exists for the default/origin remote rewrite path.

### Impact Explanation
Just as the SAM invariant let an edition owner silently change the trust assumptions users based their minting decisions on, this flow lets whoever controls the tracked GitHub repository (rename, ownership transfer, or an org restructure) silently change the trust assumption a Desktop user based their clone/collaboration decision on — where their local repository actually pushes to. Because the rewrite happens with zero UI feedback (`gitStore.setRemoteURL` → `emitUpdate`, but no popup or confirmation), a contributor could unknowingly push new commits, including private code, to a repository location they never explicitly approved, corrupting the assumed push destination silently.

### Likelihood Explanation
This path fires on ordinary, frequent background activity — background fetch, repository indicator refresh, selecting the repository — not on a rare or user-initiated action, so the exposure window is continuous for as long as the repository is tracked in Desktop. It requires no local access, no malware, and no leaked credentials; it only requires control over the GitHub-side repository object being polled, which is exactly the "attacker controls a GitHub API object" scenario called out as in-scope.

### Recommendation
Require explicit user confirmation (similar to `UpstreamAlreadyExists`) before automatically rewriting the default/origin remote URL, and/or anchor `remoteUrlUnchanged` to the URL the user actually approved at link-time rather than the most recently auto-synced value, so repeated silent drift cannot occur across multiple background refresh cycles.

### Proof of Concept
1. User clones a public GitHub repository and links it in Desktop; `origin` is `https://github.com/owner/repo`.
2. The repository owner renames or transfers the repository (or the app's stored owner/name match otherwise resolves to a different `clone_url` from the API) at any point after the user has begun committing/pushing.
3. On the next background fetch/indicator refresh (`fetchForRepositoryIndicator` → `withRefreshedGitHubRepository` → `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`), Desktop calls `gitStore.setRemoteURL('origin', apiRepo.clone_url)` with no prompt.
4. The user's next `git push` (via `performPush`) silently targets the new location, with the user never having approved the change. [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/lib/stores/app-store.ts (L4258-4272)
```typescript
  private fetchForRepositoryIndicator(repo: Repository) {
    return this.withRefreshedGitHubRepository(repo, async repo => {
      const isBackgroundTask = true
      const gitStore = this.gitStoreCache.get(repo)

      await this.withPushPullFetch(repo, () =>
        gitStore.fetch(isBackgroundTask, progress =>
          this.updatePushPullFetchProgress(repo, progress)
        )
      )
      this.updatePushPullFetchProgress(repo, null)

      return gitStore.aheadBehind
    })
  }
```

**File:** app/src/lib/stores/app-store.ts (L4874-4913)
```typescript
  private async repositoryWithRefreshedGitHubRepository(
    repository: Repository
  ): Promise<Repository> {
    const repoStore = this.repositoriesStore
    const match = await this.matchGitHubRepository(repository)

    // TODO: We currently never clear GitHub repository associations (see
    // https://github.com/desktop/desktop/issues/1144). So we can bail early at
    // this point.
    if (!match) {
      return repository
    }

    const { account, owner, name } = match
    const { endpoint } = account
    const api = API.fromAccount(account)
    const apiRepo = await api.fetchRepository(owner, name)

    if (apiRepo === null) {
      // If the request fails, we want to preserve the existing GitHub
      // repository info. But if we didn't have a GitHub repository already or
      // the endpoint changed, the skeleton repository is better than nothing.
      if (endpoint !== repository.gitHubRepository?.endpoint) {
        const ghRepo = await repoStore.upsertGitHubRepositoryFromMatch(match)
        return repoStore.setGitHubRepository(repository, ghRepo)
      }

      return repository
    }

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }

    const ghRepo = await repoStore.upsertGitHubRepository(endpoint, apiRepo)
    const freshRepo = await repoStore.setGitHubRepository(repository, ghRepo)

    await this.refreshBranchProtectionState(freshRepo)
    return freshRepo
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L18-44)
```typescript
  const remoteUrl = gitStore.defaultRemote.url
  const updatedRemoteUrl = apiRepo.clone_url
  const urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)

  // Verify that protocol hasn't changed. If it has we don't want
  // to alter the protocol in case they are relying on a specific one.
  // If protocol is null that implies the url is a ssh url
  // of the format git@github.com:octocat/Hello-World.git, which
  // can't be parsed by URL.parse. In this case we assume the user
  // manually configured their remote to use this format and we don't
  // want to change what they've done just to be safe
  const parsedRemoteUrl = URL.parse(remoteUrl)
  const parsedUpdatedRemoteUrl = URL.parse(updatedRemoteUrl)
  const protocolsMatch =
    parsedRemoteUrl.protocol !== null &&
    parsedUpdatedRemoteUrl.protocol !== null &&
    parsedRemoteUrl.protocol === parsedUpdatedRemoteUrl.protocol

  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
```

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L41-76)
```typescript
    return (
      <Dialog
        title={
          __DARWIN__ ? 'Upstream Already Exists' : 'Upstream already exists'
        }
        onDismissed={this.props.onDismissed}
        onSubmit={this.onUpdate}
        type="warning"
      >
        <DialogContent>
          <p>
            The repository <Ref>{name}</Ref> is a fork of{' '}
            <Ref>{parentName}</Ref>, but its <Ref>{UpstreamRemoteName}</Ref>{' '}
            remote points elsewhere.
          </p>
          <ul>
            <li>
              Current: <Ref>{existingURL}</Ref>
            </li>
            <li>
              Expected: <Ref>{replacementURL}</Ref>
            </li>
          </ul>
          <p>Would you like to update the remote to use the expected URL?</p>
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup
            destructive={true}
            okButtonText="Update"
            cancelButtonText="Ignore"
            onCancelButtonClick={this.onIgnore}
          />
        </DialogFooter>
      </Dialog>
    )
  }
```

**File:** app/src/lib/stores/git-store.ts (L1533-1543)
```typescript
  /** Changes the URL for the remote that matches the given name  */
  public async setRemoteURL(name: string, url: string): Promise<boolean> {
    const wasSuccessful =
      (await this.performFailableOperation(() =>
        setRemoteURL(this.repository, name, url)
      )) === true
    await this.loadRemotes()

    this.emitUpdate()
    return wasSuccessful
  }
```
