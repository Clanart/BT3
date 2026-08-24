### Title
Automatic, unconfirmed rewrite of the `origin` remote from a GitHub API `clone_url` after repository rename/transfer — ([File: app/src/lib/stores/updates/update-remote-url.ts](app/src/lib/stores/updates/update-remote-url.ts))

### Summary
The reported LiFi bug is a case of an operation trusting a value that was never validated against the value actually produced by the operation it is supposed to describe (`_lifiData.receivingAssetId` vs. `SwapData.receivingAssetId`). The Desktop analog is `updateRemoteUrl`, which silently rewrites the user's real, operational `origin` remote URL based on a `clone_url` string returned by a GitHub API lookup keyed off a *mutable* owner/name pair parsed from the current remote — with no confirmation and no pinning to a stable repository identity.

### Finding Description
`repositoryWithRefreshedGitHubRepository` derives `owner`/`name` straight from the current git remote URL via `matchGitHubRepository`, which just regex-parses the URL string: [1](#0-0) [2](#0-1) 

It then calls `api.fetchRepository(owner, name)`, whose underlying endpoint is `repos/{owner}/{name}`. GitHub's API resolves this path across renames and ownership transfers and returns the repository's *current* `clone_url`/`ssh_url`, tied only to the mutable owner/name, not to any locally-verified, immutable identity: [3](#0-2) 

The returned `apiRepo` is then handed to `updateRemoteUrl`, which compares it against the *previously recorded* `gitHubRepository.cloneURL` (an informational/display field) and, if the protocol matches and the local remote hasn't been manually edited, calls `gitStore.setRemoteURL` to rewrite the actual, operational remote used for all future `fetch`/`push` operations: [4](#0-3) [5](#0-4) 

This is invoked from the periodic/background GitHub repository refresh path (`repositoryWithRefreshedGitHubRepository`), so it can run without any explicit user action, similar to how `swapTokensGeneric()` transferred funds based on the informational `_lifiData.receivingAssetId` instead of the value actually validated by the swap execution. Here, the "informational" value is the freshly-fetched `apiRepo.clone_url`, and the "operational" value is the git remote actually used for push/fetch — the code treats the former as authoritative for reconfiguring the latter, without confirming with the user or verifying the destination is the repository the user intended to keep working with (e.g. by pinning to a stable numeric GitHub repository ID rather than a mutable owner/name string).

### Impact Explanation
An attacker who controls a public (or shared-access) GitHub repository that victims have cloned in Desktop can rename or transfer that repository to an entirely different owner/name they control. On the next background refresh, Desktop's `updateRemoteUrl` will detect the mismatch between the stored `cloneURL` and the API's new `clone_url` and silently execute `git remote set-url origin <new_url>` with no dialog, confirmation, or audit trail visible to the user. All subsequent `git push`/`git fetch` operations performed through Desktop's UI now target the attacker-chosen destination instead of the repository the user originally selected and trusted — a silent corruption of what the user pushes, matching the in-scope impact category for "silent corruption of what the user commits or pushes."

### Likelihood Explanation
The refresh path (`repositoryWithRefreshedGitHubRepository`) requires no unusual local access, admin rights, or social engineering beyond the attacker performing an ordinary GitHub action (rename/transfer) on a repository they already control access to. The guard conditions in `updateRemoteUrl` (`protocolsMatch`, `remoteUrlUnchanged`, `!urlsMatch`) are trivially satisfiable in the common case where the user has not manually edited `origin` and uses the same protocol (HTTPS or SSH) — which is the default and by far most common configuration.

### Recommendation
- Do not silently rewrite an existing remote based solely on an owner/name-keyed API lookup; require explicit user confirmation (e.g., a banner: "This repository was renamed/moved to X — update your remote?").
- Pin repository identity to GitHub's immutable numeric repository `id` (already available on `IAPIRepository`) rather than trusting a fresh owner/name lookup to imply "same repository, safe to retarget," and warn distinctly when the underlying owner account has changed (transfer) vs. only the name (rename).
- Log/expose the remote URL change in the UI so users can detect and revert unexpected retargeting.

### Proof of Concept
1. Victim clones a public repo `attacker/lib` in GitHub Desktop; `origin` is set to `https://github.com/attacker/lib.git` and `gitHubRepository.cloneURL` is recorded as the same.
2. Attacker (owner of `attacker/lib`) transfers the repository to a new account they also control, `evil-org/lib-renamed`, keeping the same underlying repository ID.
3. On Desktop's next background repository refresh, `matchGitHubRepository` parses `owner=attacker, name=lib` from the still-unchanged local remote, and `api.fetchRepository('attacker','lib')` follows GitHub's redirect, returning `clone_url: https://github.com/evil-org/lib-renamed.git`.
4. `updateRemoteUrl` finds `protocolsMatch === true`, `remoteUrlUnchanged === true` (user never touched `origin`), and `urlsMatch === false`, so it calls `gitStore.setRemoteURL('origin', 'https://github.com/evil-org/lib-renamed.git')` with no prompt, verified by the existing test asserting exactly this auto-update behavior: [6](#0-5) 
5. The victim, unaware of the silent retarget, later uses Desktop's "Push" button; their commits are pushed to `evil-org/lib-renamed` instead of the repository they originally selected.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

**File:** app/src/lib/stores/app-store.ts (L4964-4977)
```typescript
  private async matchGitHubRepository(
    repository: Repository
  ): Promise<IMatchedGitHubRepository | null> {
    const gitStore = this.gitStoreCache.get(repository)

    if (!gitStore.defaultRemote) {
      await gitStore.loadRemotes()
    }

    const remote = gitStore.defaultRemote
    return remote !== null
      ? matchGitHubRepository(this.accounts, remote.url)
      : null
  }
```

**File:** app/src/lib/repository-matching.ts (L28-46)
```typescript
/** Try to use the list of users and a remote URL to guess a GitHub repository. */
export function matchGitHubRepository(
  accounts: ReadonlyArray<Account>,
  remote: string
): IMatchedGitHubRepository | null {
  for (const account of accounts) {
    const htmlURL = getHTMLURL(account.endpoint)
    const { hostname } = URL.parse(htmlURL)
    const parsedRemote = parseRemote(remote)

    if (parsedRemote !== null && hostname !== null) {
      if (parsedRemote.hostname.toLowerCase() === hostname.toLowerCase()) {
        return { name: parsedRemote.name, owner: parsedRemote.owner, account }
      }
    }
  }

  return null
}
```

**File:** app/src/lib/api.ts (L1010-1030)
```typescript
  public async fetchRepositoryCloneInfo(
    owner: string,
    name: string,
    protocol: GitProtocol | undefined
  ): Promise<IAPIRepositoryCloneInfo | null> {
    const response = await this.ghRequest('GET', `repos/${owner}/${name}`, {
      // Make sure we don't run into cache issues when fetching the repositories,
      // specially after repositories have been renamed.
      reloadCache: true,
    })

    if (response.status === HttpStatusCode.NotFound) {
      return null
    }

    const repo = await parsedResponse<IAPIRepository>(response)
    return {
      url: protocol === 'ssh' ? repo.ssh_url : repo.clone_url,
      defaultBranch: repo.default_branch,
    }
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

**File:** app/test/unit/stores/updates/update-remote-url-test.ts (L68-81)
```typescript
  it("updates the repository's remote url when the github url changes", async t => {
    const { gitHubRepository, gitStore } = await createRepository(
      t,
      apiRepository
    )
    assert(gitStore.currentRemote !== null)

    const originalUrl = gitStore.currentRemote.url
    const updatedUrl = 'https://github.com/my-user/my-updated-repo'
    const updatedApiRepository = { ...apiRepository, clone_url: updatedUrl }
    await updateRemoteUrl(gitStore, gitHubRepository, updatedApiRepository)
    assert.notEqual(originalUrl, updatedUrl)
    assert.equal(gitStore.currentRemote.url, updatedUrl)
  })
```
