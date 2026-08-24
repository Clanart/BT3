### Title
Desktop silently adds a git `upstream` remote pointing to an attacker-controlled fork URL, sourced from the GitHub API, with no user confirmation - ([File: app/src/lib/stores/git-store.ts])

### Summary
The Push Protocol Snap report describes a broken invariant: an untrusted/semi-trusted source (a connected dapp) can cause the extension to silently mutate persistent, security-relevant state (the notification address list, the `togglepopup` config) with no real user confirmation — the "confirmation" shown to the user is a notification after the fact, not a gate. The GitHub Desktop analog of this pattern is `GitStore.addUpstreamRemoteIfNeeded` / `_addUpstreamRemoteIfNeeded` in `app/src/lib/stores/app-store.ts`, which automatically runs `git remote add upstream <url>` using a URL taken from the GitHub API's `parent.cloneURL` field, with no user prompt, confirmation dialog, or review step.

### Finding Description
When a repository is a fork, Desktop calls `addUpstreamRemoteIfNeeded` as part of normal repository refresh flow: it fetches the "parent" repository info from the GitHub API and, if no `upstream` remote already exists, silently invokes `git remote add upstream <url>` with the parent's clone URL. [1](#0-0) 

The URL used is `parent.cloneURL`, i.e. data returned by the GitHub API for whatever repository GitHub associates as the "parent" of the fork — not something the user typed or reviewed. [2](#0-1) 

This is invoked from `AppStore` behind a persisted "ignore" flag but with no confirmation UI — the only user-facing control is a one-time opt-out flag stored per-repository, not a per-addition confirmation: [3](#0-2) 

A similar pattern exists for pull-request checkout: `_findPullRequestBranch` silently calls `addRemote` using `headCloneUrl`/`headRepoOwner`, values sourced directly from a PR object returned by the GitHub API (which can describe any fork, including one created by an unrelated third party), again with no confirmation step before the remote is added to the user's local git config: [4](#0-3) 

In both cases the invariant "the user controls which remotes point where in their local git configuration" is broken by API-controlled data (an attacker who can get a PR authored against, or a fork associated with, the repository can inject an arbitrary clone URL as a remote). Existing guards (`findUpstreamRemote`, `UpstreamAlreadyExistsError`) only prevent overwriting an existing differently-configured `upstream` remote; they do nothing to validate or gate the *first* addition, and provide no confirmation dialog analogous to what the report recommends. [5](#0-4) 

### Impact Explanation
Adding an unreviewed remote is not itself code execution, but it corrupts the trust model of the repository's git configuration silently. A remote URL under attacker control can point to a malicious git server that returns crafted refs/objects on subsequent `fetch`/`pull` operations the user performs against that remote (e.g. via "fetch upstream" UI actions that operate on whatever remote is present), and it can also be used to prime targeted phishing (the remote name "upstream" implies legitimacy the user did not vet). This matches the report's "self-DoS / uncontrolled state addition" class, translated to Desktop's "silent corruption of git configuration" impact.

### Likelihood Explanation
This path triggers automatically for any forked repository the user has open in Desktop as part of routine background/foreground refresh, with the URL sourced entirely from GitHub API data (fork "parent" metadata, or PR head repo metadata) that a third party can influence by forking the repo or opening a PR — no local access, admin rights, or social engineering beyond normal collaboration is required.

### Recommendation
Before adding an `upstream` (or fork PR) remote automatically, show an explicit, blocking confirmation dialog naming the exact URL to be added and allow the user to reject or edit it, consistent with the report's recommendation to "always notify the wallet owner of important state changes and allow them to reject them." Persist confirmation choices per-URL rather than a blanket per-repository "ignore" flag so that a changed/different parent URL always requires fresh confirmation.

### Proof of Concept
1. Attacker forks the victim's public repository (or opens a pull request from their own fork) so that GitHub associates their repository as `parent`/`headRepoOwner` metadata reachable via the API.
2. Victim, using GitHub Desktop, opens their fork of the repo (or reviews the attacker's PR via "Checkout this PR").
3. `addUpstreamRemoteIfNeeded` (or `_findPullRequestBranch`) runs automatically and executes `git remote add upstream <attacker-controlled clone_url>` with no prompt. [6](#0-5) 
4. The victim's local git configuration now silently contains a remote pointing at attacker infrastructure, which can be leveraged in subsequent fetch/pull actions without further warning.

### Citations

**File:** app/src/lib/stores/git-store.ts (L1321-1356)
```typescript
  public async addUpstreamRemoteIfNeeded(): Promise<void> {
    const parent =
      this.repository.gitHubRepository &&
      this.repository.gitHubRepository.parent
    if (!parent) {
      return
    }

    const remotes = await getRemotes(this.repository)
    const upstream = findUpstreamRemote(parent, remotes)
    if (upstream) {
      return
    }

    const remoteWithUpstreamName = remotes.find(
      r => r.name === UpstreamRemoteName
    )
    if (remoteWithUpstreamName) {
      const error = new UpstreamAlreadyExistsError(
        this.repository,
        remoteWithUpstreamName
      )
      this.emitError(error)
      return
    }

    const url = forceUnwrap(
      'Parent repositories are fully loaded',
      parent.cloneURL
    )

    this._upstreamRemote =
      (await this.performFailableOperation(() =>
        addRemote(this.repository, UpstreamRemoteName, url)
      )) ?? null
  }
```

**File:** app/src/lib/stores/app-store.ts (L8585-8611)
```typescript
  private getIgnoreExistingUpstreamRemoteKey(repository: Repository): string {
    return `repository/${repository.id}/ignoreExistingUpstreamRemote`
  }

  public _ignoreExistingUpstreamRemote(repository: Repository): Promise<void> {
    const key = this.getIgnoreExistingUpstreamRemoteKey(repository)
    setBoolean(key, true)

    return Promise.resolve()
  }

  private getIgnoreExistingUpstreamRemote(
    repository: Repository
  ): Promise<boolean> {
    const key = this.getIgnoreExistingUpstreamRemoteKey(repository)
    return Promise.resolve(getBoolean(key, false))
  }

  private async addUpstreamRemoteIfNeeded(repository: Repository) {
    const gitStore = this.gitStoreCache.get(repository)
    const ignored = await this.getIgnoreExistingUpstreamRemote(repository)
    if (ignored) {
      return
    }

    return gitStore.addUpstreamRemoteIfNeeded()
  }
```

**File:** app/src/lib/stores/app-store.ts (L8633-8660)
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
```

**File:** app/src/lib/stores/helpers/find-upstream-remote.ts (L8-22)
```typescript
/**
 * Find the upstream remote based on the parent repository and the list of
 * remotes.
 */
export function findUpstreamRemote(
  parent: GitHubRepository,
  remotes: ReadonlyArray<IRemote>
): IRemote | null {
  const upstream = remotes.find(r => r.name === UpstreamRemoteName)
  if (!upstream) {
    return null
  }

  return repositoryMatchesRemote(parent, upstream) ? upstream : null
}
```
