## Analysis

The TWAP report's broken invariant is: **a single untrusted external data point (the manipulated pool price) is blindly trusted to overwrite a security-critical value** (collateral valuation) with no sanity/consistency check against the pre-existing trusted state, letting an attacker who controls that data source cause silent, unauthorized state corruption.

The Desktop analog is in `updateRemoteUrl`, which trusts the `clone_url` field of a GitHub API repository object to silently rewrite the local `origin` remote — a security-critical value that determines where future `git push`/fetch and credential-helper traffic goes.

### Title
Unvalidated GitHub API `clone_url` silently rewrites the local git remote, allowing push/credential redirection - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` is invoked automatically, without user interaction, every time Desktop refreshes a repository's GitHub metadata (background fetch, focus refresh, manual fetch, etc.) via `repositoryWithRefreshedGitHubRepository`. It takes the `clone_url` returned by the GitHub API for the matched repository and, if a same-protocol and "remote hasn't been manually diverged" heuristic passes, calls `gitStore.setRemoteURL` to overwrite the user's `origin` remote — with **no verification that the new URL's hostname matches the account's expected endpoint/host**.

### Finding Description
The call chain is:
- `BackgroundFetcher.performAndScheduleFetch` / `_fetch` → `withRefreshedGitHubRepository` → `repositoryWithRefreshedGitHubRepository` [1](#0-0) 
- `matchGitHubRepository` resolves the account purely by comparing the existing remote's hostname to a configured account's endpoint hostname [2](#0-1) 
- `api.fetchRepository(owner, name)` returns an `IAPIRepository` object whose `clone_url` field is attacker-influenceable data (an "attacker-controlled GitHub API object" per the report's own valid-impact definition — e.g. a compromised/MITM'd GitHub Enterprise Server endpoint, or a malicious/compromised repo host that the account is configured against).
- `updateRemoteUrl` then does:
```
app/src/lib/stores/updates/update-remote-url.ts:18-44
const remoteUrl = gitStore.defaultRemote.url
const updatedRemoteUrl = apiRepo.clone_url
...
const protocolsMatch = ... parsedRemoteUrl.protocol === parsedUpdatedRemoteUrl.protocol
const remoteUrlUnchanged = urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)
if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
  await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
}
``` [3](#0-2) 

The only two guards are: (1) URL scheme (`https`/`ssh`) must match, and (2) the *current* remote must not have already diverged from the previously cached `cloneURL` (a heuristic meant only to respect manual user edits). **Neither guard restricts the new `clone_url`'s hostname/owner to the expected GitHub endpoint.** `urlMatchesRemote` is only used to detect whether URLs are the *same*, never to validate that the *new* URL stays within the trusted host. `URL.parse` only extracts the protocol, so `https://attacker.example.com/anything.git` passes `protocolsMatch` just as easily as a legitimate `https://github.com/owner/repo.git` would.

This directly mirrors the oracle-manipulation invariant break: a value pulled from an external, attacker-reachable source (`apiRepo.clone_url`) is trusted and used to silently mutate a security-critical local value (the `origin` remote URL) with a consistency check that validates *identity of state transition*, not *trustworthiness of the new value's origin*.

### Impact Explanation
`git remote set-url` executed via `gitStore.setRemoteURL` [4](#0-3)  silently redirects the destination of the user's next `git push`, and any git credential-helper/askpass flow tied to that remote's host. Since this happens transparently on a background refresh cycle (no diff, no confirmation dialog, no notification of the URL change), a victim could unknowingly push commits (and, depending on credential-helper configuration, leak token material during the push handshake) to an attacker-controlled endpoint. This satisfies the report's valid-impact class of "attacker controls...a GitHub API object" causing "silent corruption of what the user commits or pushes" / "credential exfiltration."

### Likelihood Explanation
This requires the account's configured API endpoint (in practice, a GitHub Enterprise Server instance, since github.com itself is authoritative and not attacker-forgeable) to return a manipulated repository object — e.g., via a compromised/malicious GHES instance, an on-path/MITM position against that endpoint, or a GHES admin/insider modifying repo metadata. Given GitHub Desktop explicitly supports GHE/GHES accounts with independently configured, potentially less-trusted endpoints, and the refresh happens automatically and periodically (`BackgroundFetcher`, `DefaultFetchInterval` [5](#0-4) ), the exploitation window recurs continuously without any user action.

### Recommendation
In `updateRemoteUrl`, before calling `setRemoteURL`, additionally validate that the new `clone_url`'s hostname matches the hostname of the account's configured API endpoint (or the existing remote's hostname), not just the URL scheme. Consider also surfacing a non-silent confirmation/notification when the remote URL is being changed automatically, rather than performing the mutation invisibly in a background refresh path.

### Proof of Concept
1. Victim adds a GitHub Enterprise account in Desktop and clones a repo hosted on that GHES instance; `origin` is set to `https://ghes.corp.example/owner/repo.git`.
2. Attacker gains the ability to tamper with responses from that endpoint for `GET repos/{owner}/{name}` (e.g., a compromised/MITM'd GHES instance, matching the report's "attacker controls a GitHub API object" primitive) and returns `clone_url: "https://attacker-controlled.example/owner/repo.git"`.
3. On the next background fetch (`BackgroundFetcher` → `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`), since `protocolsMatch` (both `https`) and `remoteUrlUnchanged` (user never manually edited the remote) hold true while `urlsMatch` is false, Desktop silently executes `git remote set-url origin https://attacker-controlled.example/owner/repo.git` [6](#0-5) .
4. The victim's next `git push` (and any associated credential handshake) is transparently sent to `attacker-controlled.example` instead of the intended GHES host, with no dialog, diff, or warning shown.

### Citations

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

**File:** app/src/lib/repository-matching.ts (L29-46)
```typescript
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

**File:** app/src/lib/git/remote.ts (L56-64)
```typescript
/** Changes the URL for the remote that matches the given name  */
export async function setRemoteURL(
  repository: Repository,
  name: string,
  url: string
): Promise<true> {
  await git(['remote', 'set-url', name, url], repository.path, 'setRemoteURL')
  return true
}
```

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L7-17)
```typescript
/**
 * A default interval at which to automatically fetch repositories, if the
 * server doesn't specify one or the header is malformed.
 */
const DefaultFetchInterval = 1000 * 60 * 60

/**
 * A minimum fetch interval, to protect against the server accidentally sending
 * us a crazy value.
 */
const MinimumInterval = 1000 * 5 * 60
```
