## Analysis

The TWAP report's underlying flaw is: **a critical value is derived from a single untrusted external observation and used to drive an automatic state change, without any check that the new value is consistent with a trusted baseline (host/origin).**

The GitHub Desktop analog is `updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts`, which is invoked automatically (no user interaction) from `repositoryWithRefreshedGitHubRepository` in `app/src/lib/stores/app-store.ts` every time the app refreshes GitHub repository metadata via the API.

### Title
Automatic remote-URL rewrite trusts a single unverified `clone_url` field from the GitHub API, allowing silent remote hijack - (`File: app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` silently runs `git remote set-url` to change the user's `origin` remote whenever the GitHub API's `clone_url` for the associated repository differs from the current remote, as long as the protocol scheme (`https:`/`https:` or `ssh:`/`ssh:`) matches and the remote hadn't been "manually" changed. It never verifies that the new URL's **host** matches the account's endpoint or the previously trusted host, mirroring the TWAP bug's failure to validate that a new price observation is consistent with a trusted historical baseline.

### Finding Description [1](#0-0) 

The function:
1. Reads `apiRepo.clone_url` — a single field coming directly from a GitHub API response (or a GitHub Enterprise Server response, which can traverse organization-controlled infrastructure/proxies).
2. Computes `protocolsMatch` by comparing only the URL *scheme* (`https:` vs `ssh:`), not the hostname/authority.
3. Computes `remoteUrlUnchanged` by checking the *current* remote still matches the *previously cached* `gitHubRepository.cloneURL` (the last trusted value), again via `urlMatchesRemote`, which does compare hostnames — but only between the **old** cached URL and the **current** remote, never between the **old** and the **new (incoming)** URL.
4. If `protocolsMatch && remoteUrlUnchanged && !urlsMatch`, it calls `gitStore.setRemoteURL(...)`, silently rewriting the local `origin` remote to whatever host the API response specified — with no user confirmation, and no comparison of the new hostname against the account's own endpoint (`getHTMLURL(account.endpoint)`), which is otherwise used elsewhere in the codebase (e.g. `matchGitHubRepository`) precisely to pin trust to a known host.

This is invoked from the periodic background refresh path: [2](#0-1) 

Just like the TWAP oracle only checked "was there *a* price update" without validating how many/how trustworthy the observations were, this code only checks "did the scheme stay the same" without validating the destination host is legitimate — the single untrusted data point (`apiRepo.clone_url`) is trusted outright.

### Impact Explanation
If an attacker can influence the API response for a tracked repository (e.g., a compromised/malicious GitHub Enterprise Server the account is configured against, or a network proxy/MITM position on the API traffic — both explicitly in-scope attacker positions), Desktop will silently repoint the user's `origin` remote to an attacker-controlled Git host. Subsequent `git push` operations (and any credential-helper authentication attempts against that host) go to the attacker's server instead of the real one — this is silent corruption of what the user pushes and a vector for credential exfiltration, matching the report's "attacker controls ... a git remote/proxy response" impact category.

### Likelihood Explanation
Likelihood is Medium: it requires the attacker to control (or MITM) the specific API endpoint/host the user's account is configured against, which is a real but non-trivial precondition (most realistic for self-hosted GitHub Enterprise Server deployments with a compromised or attacker-operated server/proxy in the path). No local access, admin rights, or social engineering of the victim is required — the rewrite happens automatically during a routine background refresh cycle.

### Recommendation
Before calling `gitStore.setRemoteURL`, validate that the new URL's hostname matches either the previous trusted hostname or the account's own configured endpoint hostname (as is already done in `matchGitHubRepository`/`urlMatchesRemote` elsewhere). Do not silently rewrite remotes across hosts; at minimum require explicit user confirmation when the destination host changes.

### Proof of Concept
1. User has a repository whose `origin` remote and `gitHubRepository.cloneURL` both point to `https://ghe.corp.example/org/repo.git` (a GHE instance).
2. Attacker compromises the GHE instance/proxy (or performs a MITM on that specific host) and, on the next `fetchRepository` call triggered by Desktop's periodic background refresh, returns an API payload where `clone_url` = `https://attacker.example/org/repo.git`.
3. In `updateRemoteUrl`: `protocolsMatch` is true (both `https:`), `remoteUrlUnchanged` is true (local remote still equals old cached `cloneURL`), `urlsMatch` is false (new host differs) → condition passes.
4. `gitStore.setRemoteURL('origin', 'https://attacker.example/org/repo.git')` executes automatically with no prompt.
5. The user's next `git push` sends commits (and potentially credentials via the credential helper) to `attacker.example`.

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
