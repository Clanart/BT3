## Finding

### Title
Unauthenticated remote-URL takeover via crafted GitHub API `clone_url` — (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
GitHub Desktop periodically "refreshes" a tracked repository's association with its GitHub counterpart by calling the GitHub API and, based on the response, silently rewriting the local `origin` remote's URL. The function that performs this rewrite validates only that the *protocol* (`https`/`ssh`) of the new URL matches the old one — it never validates that the *host, owner, or repository name* of the new URL still corresponds to the repository the user actually has. A malicious or compromised API endpoint (e.g., a rogue/compromised GitHub Enterprise Server account added by the user, or any server impersonated at that endpoint) can therefore respond with an arbitrary `clone_url` and cause Desktop to silently repoint the user's `origin` remote to attacker-controlled infrastructure.

### Finding Description
The refresh flow lives in `repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts`: [1](#0-0) 

It fetches `apiRepo` from the API using `owner`/`name` derived from the existing local remote, then unconditionally calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`. That helper, in `app/src/lib/stores/updates/update-remote-url.ts`, performs the actual mutation: [2](#0-1) 

The only guards before calling `gitStore.setRemoteURL(...)` are:
- `protocolsMatch` — only compares the URL *scheme*, not host/owner/name.
- `remoteUrlUnchanged` — verifies the *previously stored* `gitHubRepository.cloneURL` still matches the current local remote (i.e., that the user hasn't manually repointed the remote).
- `!urlsMatch` — the new URL differs from the current remote.

There is no check anywhere in this path (or in `urlMatchesRemote` from `app/src/lib/repository-matching.ts`, which is only used to detect *whether* the URL changed, not whether the change is legitimate) that the new `clone_url` still points to the same hostname/owner/name that was originally being tracked: [3](#0-2) 

So as long as the new URL uses the same protocol and the old stored `cloneURL` still matches the local remote, Desktop will happily call `setRemoteURL` with whatever `clone_url` the API response contains — including a completely different host such as `https://evil.example.com/anything/anything.git`.

This mirrors the reported bug-class exactly: state (`updateVerifier`/here, the trusted remote) is reassigned based on new authority-controlled input (`apiRepo.clone_url`) using guard conditions that check a superficial invariant (protocol match) instead of the invariant that actually matters (identity of the destination host/repo), silently corrupting the trusted state.

### Impact Explanation
`origin` (and other remotes referenced by the app, e.g. via `_convertRepositoryToFork`'s similar pattern at `app/src/lib/stores/app-store.ts:8965-8991`) is the destination Desktop uses for all subsequent `git push`/`git fetch`/`git pull` operations for that repository. Silently repointing it means:
- Future `git push` operations from the user send their commits (and potentially credentials, since Git credential helpers key off the remote host) to an attacker-controlled server.
- Future `git fetch`/`pull` operations pull attacker-controlled objects into the user's local repository, which can then be merged and re-committed — a supply-chain/content-integrity compromise.

This satisfies the "silent corruption of what the user commits or pushes" and "credential exfiltration" categories in the allowed impact list, driven purely by a "GitHub API object" the attacker controls.

### Likelihood Explanation
This refresh path runs automatically as part of normal repository housekeeping (e.g., `_addRepositories` calls `repositoryWithRefreshedGitHubRepository` per repository) — no unusual user action is required beyond having previously added an account (including a GitHub Enterprise Server account) whose API endpoint later returns a malicious response, or being subject to a compromise of that endpoint's response for a single API call. Because the check only rejects protocol mismatches, an attacker fully controlling that account's API responses (which is within the accepted "attacker controls...a GitHub API object" premise) can trigger this on the very next background refresh.

### Recommendation
In `updateRemoteUrl`, before calling `gitStore.setRemoteURL`, additionally verify that the new `apiRepo.clone_url` resolves to the same hostname (and ideally the same owner/name identity established via `matchGitHubRepository`) as the account's endpoint / the repository originally being tracked, not merely that the protocol matches. Reject or prompt the user for confirmation if the destination host changes.

### Proof of Concept
1. Add a GitHub Enterprise Server account in Desktop pointing at `https://ghe.example.com`.
2. Clone/track a repository whose `origin` is `https://ghe.example.com/acme/widgets.git`.
3. Have the endpoint's `GET repos/acme/widgets` API response (attacker-controlled, e.g., via a compromised/rogue GHES instance) return `"clone_url": "https://attacker.example.com/acme/widgets.git"` with the same `https` protocol.
4. On the next background refresh (`repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`), Desktop calls `gitStore.setRemoteURL('origin', 'https://attacker.example.com/acme/widgets.git')` because `protocolsMatch` is true and `remoteUrlUnchanged` is true — with no host/owner check.
5. The next `git push`/`git fetch` performed by the user via Desktop's UI silently goes to `attacker.example.com` instead of `ghe.example.com`. [4](#0-3)

### Citations

**File:** app/src/lib/stores/app-store.ts (L4887-4907)
```typescript
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
