### Title
Automatic remote-URL rewrite trusts unverified GitHub API `clone_url` field, allowing silent redirection of push/fetch target - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` silently calls `gitStore.setRemoteURL()` to overwrite the user's configured `origin` remote whenever it decides the user hasn't manually changed it. The "hasn't been manually changed" and "safe to update" checks only validate two narrow properties (URL protocol equality and whether the *current* remote still matches the previously known `cloneURL`) — they never verify that the *new* value (`apiRepo.clone_url`, an untrusted field coming from the GitHub/GHES API response) still refers to the same repository identity (owner/name/host) as before the update. This mirrors the JBSplitsStore bug pattern: a "sameness"/lock-preserving check that validates only a subset of the relevant fields, letting the rest of the object be silently overwritten.

### Finding Description
The gating logic is: [1](#0-0) 

- `remoteUrlUnchanged` only confirms the *current* remote still equals the previously cached `gitHubRepository.cloneURL` (i.e., the user hasn't manually edited it).
- `protocolsMatch` only confirms the URL scheme (https/ssh) hasn't changed.
- Neither check constrains what the *new* URL (`apiRepo.clone_url`) is allowed to be. There is no verification that the new clone URL's owner/name/host still corresponds to the same GitHub repository identity that the user originally added/cloned.

When both loose conditions hold, the function unconditionally writes the API-supplied URL as the git remote: [2](#0-1) 

The `apiRepo` is fetched from `api.fetchRepository(owner, name)` against whatever endpoint/account was resolved for the repository, and is the same kind of live, externally-served response that other pipelines rely on: [3](#0-2) 

This is analogous to the JBSplitsStore bug: the "lock" (i.e., "don't silently change the user's configured push/fetch target without their knowledge") is only partially enforced — matching on protocol but omitting the equivalent of `preferClaimed`/`preferAddToBalance`, here the omitted fields are the destination host/owner/name of the new URL itself.

### Impact Explanation
If an attacker can influence the GitHub/GitHub Enterprise Server API response for the repository the user has open (e.g., a compromised or attacker-operated GHES instance the user has added as an account, or a network position able to answer that account's API traffic), the attacker can set `clone_url` to any value satisfying only "same protocol." Desktop will then silently rewrite the user's `origin` remote to that attacker-chosen URL via `gitStore.setRemoteURL`. Subsequent `git push`/`git fetch` operations initiated from the Desktop UI would silently target the attacker's URL instead of the user's actual repository — corrupting what the user believes they are pushing to/pulling from, and depending on the credential helper's host matching, potentially causing the user's push credentials to be presented to an attacker-controlled host.

### Likelihood Explanation
This path requires the attacker to control (or be able to spoof) an API response reachable by the app for an account the user has configured (most plausible for self-hosted/Enterprise endpoints, or an on-path attacker against an endpoint), and requires the repository to be refreshed via `repositoryWithRefreshedGitHubRepository` (a routine periodic/foreground refresh path). No local access, admin rights, or social engineering step by the user is required beyond having the repo open in Desktop with that account signed in.

### Recommendation
Before rewriting the git remote, additionally verify that the new `apiRepo.clone_url` still matches the same repository identity as the currently associated `GitHubRepository` (e.g., compare `apiRepo.id`/`dbID`, or at minimum owner+name+host) — not just protocol equality — mirroring the fix pattern of adding the missing sameness checks (`preferClaimed`, `preferAddToBalance`) in the original JBSplitsStore mitigation.

### Proof of Concept
1. User has a repository cloned from `github.example.com/org/repo` with a self-hosted GitHub Enterprise account added in Desktop.
2. The GHES endpoint (attacker-controlled or on-path attacker) responds to `fetchRepository(owner, name)` with `clone_url` pointing to `https://attacker.example.com/org/repo.git` (same protocol, different host).
3. `repositoryWithRefreshedGitHubRepository` invokes `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`.
4. `protocolsMatch` is true (both `https`), `remoteUrlUnchanged` is true (user hasn't manually edited origin), `urlsMatch` is false (host differs) → the guard condition is satisfied and `gitStore.setRemoteURL('origin', 'https://attacker.example.com/org/repo.git')` executes, silently repointing the user's remote. [4](#0-3)

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

**File:** app/src/lib/stores/app-store.ts (L4886-4907)
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
