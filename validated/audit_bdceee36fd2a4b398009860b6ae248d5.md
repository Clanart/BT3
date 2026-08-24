### Title
`updateRemoteUrl()` silently rewrites the local git remote to an attacker-influenced `clone_url` returned by a GitHub/GHES API response, with no user confirmation - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
The Sherlock finding is a case of "stale trust data not reset/reverified before it's used to route future value transfers, allowing a previously-trusted party to keep receiving what should now go to a new owner." The closest structural analog in GitHub Desktop is `updateRemoteUrl()`, which is invoked automatically (on every repository selection and background refresh) and re-points the user's local `origin` remote URL to whatever `clone_url` the API/host associated with the repository currently returns — without any explicit user confirmation and based only on a heuristic "did the user manually change it" check, not on verifying the destination is still legitimate.

### Finding Description
`repositoryWithRefreshedGitHubRepository()` runs on repository selection and on every background-fetch cycle: [1](#0-0) 

It fetches the repository object from the API for the account/owner/name match, and if the local remote hasn't been "manually changed" (per a URL comparison, not per any cryptographic or identity check), it calls `updateRemoteUrl()`, which will silently call `gitStore.setRemoteURL()`: [2](#0-1) 

The guard conditions are:
- `protocolsMatch`: only requires that both the old and new URL scheme (e.g. `https`) match — it does not validate hostname.
- `remoteUrlUnchanged`: only checks that the *current* local remote still matches the *previously stored* `gitHubRepository.cloneURL` — i.e. it assumes "if the user hasn't touched it since we last knew about it, it's safe to auto-update it to whatever the API says now."

This is the same broken invariant as the Sherlock bug: a value (`payee` / remote URL) that should be re-derived from the *current* trusted owner/source is instead carried forward and only opportunistically refreshed from an untrusted or stale channel, without positively re-verifying the new destination is legitimate. In Desktop's case, the "untrusted channel" is the API response for the matched repository — for GitHub Enterprise Server hosts (or any endpoint reachable via a compromised/malicious network intermediary — the task's allowed "git remote/proxy response" attacker model), `apiRepo.clone_url` is attacker-influenceable. This function trusts that value implicitly and rewrites the on-disk git config's remote URL for the user, with no dialog, confirmation, or diff shown.

### Impact Explanation
If an attacker controls the response of the GitHub/GHES API used to refresh a repository's metadata (e.g. a compromised/malicious enterprise host or a network position between Desktop and that host — explicitly in scope per the "git remote/proxy response" attacker model), they can cause Desktop to **silently repoint the user's origin remote to an attacker-controlled URL** the next time the repository is opened or auto-refreshed in the background (`_selectRepositoryRefreshTasks` → `repositoryWithRefreshedGitHubRepository`, and `fetchForRepositoryIndicator` via `withRefreshedGitHubRepository`): [3](#0-2) [4](#0-3) 

The direct, corrupted value is the git remote (`origin`) URL — the exact target that subsequent `git push`/`git fetch` operations will use. This satisfies the report's allowed impact category of "silent corruption of what the user commits or pushes," since the user has no indication their pushes are now targeting a different destination than the one they originally configured. Depending on how the trampoline credential helper subsequently resolves credentials for the new host, there is additional risk of credential/token being sent toward the attacker-controlled endpoint if it matches an account's endpoint heuristically, though I was not able to fully trace `getGitHubCredential`'s host-matching logic within the remaining budget — this should be verified by a background agent before final write-up.

### Likelihood Explanation
This code path runs unconditionally and repeatedly as part of normal usage — on every repository selection and every background-fetch/indicator-refresh cycle — with no feature flag or opt-out, and requires zero unusual user action. The only precondition is that the user has previously not manually diverged their remote URL from what Desktop last recorded, which is the common case for the vast majority of users who never touch remote settings manually. The `TODO` comment in the code itself (`// TODO: We currently never clear GitHub repository associations`) also shows the underlying association/trust-refresh logic is known to be under-specified by the maintainers.

### Recommendation
`updateRemoteUrl()` should not silently rewrite the git remote based solely on protocol match and "was it unchanged since we last checked." At minimum:
1. Require the hostname of the previous and new URL to be validated against the account's known/pinned endpoint (not just protocol), so a same-protocol but different-host redirect from an untrusted/compromised endpoint is rejected outright.
2. Surface a confirmation prompt to the user before automatically changing `origin`'s URL, similar to how `RepositorySettings` requires explicit user action via `onSubmit` when manually editing the remote: [5](#0-4) 
3. Log/audit automatic remote URL rewrites distinctly so users can detect unexpected redirections.

### Proof of Concept
1. User has a repository in Desktop associated with a GitHub Enterprise Server account whose `origin` remote is `https://ghes.company.com/org/repo.git`, matching `gitHubRepository.cloneURL`.
2. Attacker controls (compromises, or sits as a network proxy in front of) the GHES host, or otherwise can influence the API response Desktop receives for `api.fetchRepository(owner, name)`.
3. Attacker's crafted API response sets `clone_url` to `https://ghes.company.com/attacker/evil.git` (same protocol/host prefix passes surface checks — the code only checks protocol, not full authority).
4. On the user's next repository selection or background refresh tick, `repositoryWithRefreshedGitHubRepository()` → `updateRemoteUrl()` finds `protocolsMatch === true` and `remoteUrlUnchanged === true` (user never touched it) and `urlsMatch === false`, so it calls `gitStore.setRemoteURL('origin', 'https://ghes.company.com/attacker/evil.git')` — silently rewriting `.git/config` with no prompt.
5. The user's next `git push`/`git fetch` now silently targets the attacker's repository instead of their intended one.

Note: I could not fully verify within available tool calls whether the trampoline credential helper (`app/src/lib/trampoline/trampoline-credential-helper.ts`) would also route the account's push credentials toward this attacker-controlled host in this exact scenario; that secondary credential-exfiltration claim should be independently confirmed by a Devin session with full repo access before being treated as confirmed.

### Citations

**File:** app/src/lib/stores/app-store.ts (L2255-2257)
```typescript
    this.addUpstreamRemoteIfNeeded(repository)

    return this.repositoryWithRefreshedGitHubRepository(repository)
```

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

**File:** app/src/lib/stores/app-store.ts (L4874-4907)
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
```

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

**File:** app/src/ui/repository-settings/repository-settings.tsx (L296-313)
```typescript
    if (this.state.remote && this.props.remote) {
      const trimmedUrl = this.state.remote.url.trim()

      if (trimmedUrl !== this.props.remote.url) {
        try {
          await this.props.dispatcher.setRemoteURL(
            this.props.repository,
            this.props.remote.name,
            trimmedUrl
          )
        } catch (e) {
          log.error(
            `RepositorySettings: unable to set remote URL at ${this.props.repository.path}`,
            e
          )
          errors.push(`Failed setting the remote URL: ${e}`)
        }
      }
```
