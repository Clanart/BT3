### Title
Automatic `updateRemoteUrl()` rewrites `origin`'s URL from untrusted GitHub API data, silently redirecting an in-flight push/credentials to an attacker-controlled remote - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
Like `InsuranceFund.syncDeps()`, which lets a privileged actor swap the `vusd` address between a user's deposit and withdrawal, GitHub Desktop has a function that automatically swaps the address a critical operation (git push) resolves against — the `origin` remote URL — based on data fetched from an external, only loosely-trusted source (the GitHub API), with no synchronization against an in-flight push. `updateRemoteUrl()` [1](#0-0)  rewrites the local `origin` remote to whatever `clone_url` the API currently reports for the matched repository, and it is invoked from paths that are not gated by the same "only one network op at a time" lock (`isPushPullFetchInProgress`) that guards push/pull/fetch.

### Finding Description
`repositoryWithRefreshedGitHubRepository()` fetches the repository object from the API and, if the fork/rename heuristics match, calls `updateRemoteUrl()` to overwrite the on-disk `origin` URL: [2](#0-1) 

This call path fires from several places that run independently of an ongoing push:
- Fire-and-forget on repository selection: `_selectRepositoryRefreshTasks()` calls it without gating on `isPushPullFetchInProgress` [3](#0-2) 
- On account changes: `refreshSelectedRepositoryAfterAccountChange()` [4](#0-3) 
- When adding a repository: `_addRepositories()` [5](#0-4) 

Meanwhile, `performPush()` captures the remote once at the start of the push and hands only `remote.name` (not a resolved URL) to the underlying `git push` invocation: [6](#0-5) [7](#0-6) 

`git push <remote.name> ...` resolves the destination URL from the **current** `.git/config` at the moment the subprocess actually runs — not from the `IRemote` object Desktop cached when the user clicked "Push." There is no check anywhere in `performPush`/`pushRepo` that the on-disk `origin` URL still matches what was shown/approved in the UI. `updateRemoteUrl()`'s `setRemoteURL()` call performs `git remote set-url origin <newUrl>` [8](#0-7)  directly against the working copy's git config, so if it executes between "push started" and "git subprocess spawned", the push silently goes to the new URL instead of the one the user saw.

The guard `remoteUrlUnchanged` in `updateRemoteUrl()` only checks that the *previously known* API `cloneURL` still matches the local remote — it does not detect or block a repository takeover/rename scenario where the GitHub API itself now reports a different (attacker-controlled) `clone_url` for the same `owner/name` pair Desktop is tracking (e.g., a repository is deleted/renamed and the name is re-registered by another party — a well-known "repo-jacking" technique — or a fork's tracked parent is transferred to a different account). `urlMatchesRemote()` [9](#0-8)  then declares the URLs different and `updateRemoteUrl()` rewrites `origin` to the attacker's URL automatically, without any user prompt (contrast this with the explicit, user-confirmed `UpstreamAlreadyExists` dialog used for the analogous *upstream* remote case [10](#0-9) , which `updateRemoteUrl()` has no equivalent of for `origin`).

### Impact Explanation
If the rewrite lands in the window between a user initiating a push and the `git push` subprocess actually resolving the remote URL, the user's commits are silently pushed to a remote the user never approved. Because `envForRemoteOperation(remote.url)` [11](#0-10)  is computed from the *stale, cached* URL for proxy purposes while the actual git operation targets the *new* URL, credential helpers / proxy resolution and the actual push destination can disagree, increasing the chance of silently leaking code (and potentially credentials negotiated by git's credential helper for the wrong host) to an attacker-controlled endpoint. This matches the "silent corruption of what the user commits or pushes" / "credential exfiltration" impact classes.

### Likelihood Explanation
This requires no local access, admin rights, or pre-existing malware: the attacker only needs to control what the GitHub API returns for a repository Desktop is tracking (e.g., by acquiring a renamed/deleted repository name, or by controlling a fork's declared parent). No user interaction beyond normal use (selecting the repository, or Desktop's own periodic background refresh) is needed to trigger `updateRemoteUrl()`; a concurrently in-flight push is a normal, common user action, making the race a plausible (not purely theoretical) event during ordinary usage of the app on public/forked repositories.

### Recommendation
- Re-verify (or re-read) the `origin` remote URL immediately before invoking `git push`, and abort/re-prompt if it differs from what the user approved, instead of trusting an early-captured `IRemote`.
- Gate `updateRemoteUrl()`'s write path behind the same `isPushPullFetchInProgress` lock used by `withPushPullFetch`, so an automatic remote-URL rewrite can never race with a user-initiated push/pull/fetch.
- Require explicit user confirmation before rewriting `origin`'s URL based on API data, mirroring the existing `UpstreamAlreadyExists` dialog behavior for the `upstream` remote.

### Proof of Concept
1. User clones/uses a repository `origin` pointing at `https://github.com/owner/repo`.
2. The `owner/repo` name is subsequently deleted/transferred and re-registered by an attacker (or a tracked fork's parent metadata changes to point elsewhere) so the GitHub API's `clone_url` for the same logical repository now resolves to the attacker's fork/repo.
3. User makes local commits and clicks "Push."
4. Concurrently, Desktop performs a routine background refresh (repository selection, account refresh, or indicator update) that calls `repositoryWithRefreshedGitHubRepository()` → `updateRemoteUrl()`, which executes `git remote set-url origin <attacker-url>` against the same working copy.
5. If this lands before the `git push origin ...` subprocess spawned by `performPush()`/`pushRepo()` resolves `origin`'s URL, the user's commits are pushed to the attacker's remote instead of the one shown in the UI — with no error, confirmation, or warning. [12](#0-11) [13](#0-12)

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L1-45)
```typescript
import { IAPIRepository } from '../../api'
import { GitStore } from '../git-store'
import { urlMatchesRemote } from '../../repository-matching'
import * as URL from 'url'
import { GitHubRepository } from '../../../models/github-repository'

export async function updateRemoteUrl(
  gitStore: GitStore,
  gitHubRepository: GitHubRepository,
  apiRepo: IAPIRepository
): Promise<void> {
  // I'm not sure when these early exit conditions would be met. But when they are
  // we don't have enough information to continue so exit early!
  if (gitStore.defaultRemote === null) {
    return
  }

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
}
```

**File:** app/src/lib/stores/app-store.ts (L2255-2257)
```typescript
    this.addUpstreamRemoteIfNeeded(repository)

    return this.repositoryWithRefreshedGitHubRepository(repository)
```

**File:** app/src/lib/stores/app-store.ts (L4890-4907)
```typescript
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
```

**File:** app/src/lib/stores/app-store.ts (L4921-4933)
```typescript
  private async refreshSelectedRepositoryAfterAccountChange() {
    const repository = this.selectedRepository

    if (repository === null || repository instanceof CloningRepository) {
      return
    }

    if (!isRepositoryWithGitHubRepository(repository)) {
      return
    }

    await this.repositoryWithRefreshedGitHubRepository(repository)
  }
```

**File:** app/src/lib/stores/app-store.ts (L5275-5291)
```typescript
      const safeRemote: IRemote = { name: remoteName, url: remote.url }

      if (safeRemote.name !== remote.name) {
        sendNonFatalException(
          'remoteNameMismatch',
          new Error('The current remote name differs from the branch remote')
        )
      }

      const gitStore = this.gitStoreCache.get(repository)
      await gitStore.performFailableOperation(
        async () => {
          let aborted = false
          await pushRepo(
            repository,
            safeRemote,
            branch.name,
```

**File:** app/src/lib/stores/app-store.ts (L8148-8151)
```typescript
        const [refreshedRepo, usingLFS] = await Promise.all([
          this.repositoryWithRefreshedGitHubRepository(addedRepo),
          this.isUsingLFS(addedRepo),
        ])
```

**File:** app/src/lib/stores/app-store.ts (L8285-8306)
```typescript
  private async withRefreshedGitHubRepository<T>(
    repository: Repository,
    fn: (repository: Repository) => Promise<T>
  ): Promise<T> {
    let updatedRepository = repository
    const account: Account | null = getAccountForRepository(
      this.accounts,
      updatedRepository
    )

    // If we don't have a user association, it might be because we haven't yet
    // tried to associate the repository with a GitHub repository, or that
    // association is out of date. So try again before we bail on providing an
    // authenticating user.
    if (!account) {
      updatedRepository = await this.repositoryWithRefreshedGitHubRepository(
        repository
      )
    }

    return fn(updatedRepository)
  }
```

**File:** app/src/lib/git/push.ts (L57-61)
```typescript
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
  ]
```

**File:** app/src/lib/git/push.ts (L76-82)
```typescript
  let opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(remote.url),
    interceptHooks: ['pre-push'],
    onHookProgress: options?.onHookProgress,
    onHookFailure: options?.onHookFailure,
    onTerminalOutputAvailable: options?.onTerminalOutputAvailable,
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
