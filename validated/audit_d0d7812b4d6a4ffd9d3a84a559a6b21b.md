## Finding

The strongest local analog for this bug class is in `updateRemoteUrl`, which silently rewrites a repository's git remote URL based on data returned from the GitHub API, checking only that the *protocol* stayed the same instead of checking that the *host* stayed the same — the same broken-invariant pattern as `zapWETH` checking LP count but not price.

### Title
Silent remote-URL rewrite based on attacker-influenced API `clone_url` lacks hostname validation - (`app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` automatically overwrites the local git remote when the GitHub API's `clone_url` for a tracked repository differs from the configured remote. The guard only verifies that the URL *protocol* (`https:`/`ssh:`) is unchanged — it never verifies that the *hostname* is unchanged before calling `gitStore.setRemoteURL`.

### Finding Description [1](#0-0) 

The relevant checks are:
- `urlsMatch` — whether the current remote structurally equals the new `clone_url` (hostname+owner+name).
- `protocolsMatch` — only compares `protocol` (`https:` vs `ssh:`-style scheme), via `URL.parse`.
- `remoteUrlUnchanged` — whether the previously cached `gitHubRepository.cloneURL` still equals the current git remote (i.e., the user hasn't manually edited the remote).

If `protocolsMatch && remoteUrlUnchanged && !urlsMatch`, the code calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` — writing the API-provided URL directly into `.git/config` — with **no check that the hostname of `updatedRemoteUrl` matches the hostname of the existing remote**. `urlMatchesRemote`, which does compare hostname/owner/name, is only used to decide *whether* an update is needed, not to constrain *what* the update can be. The recommendation from the source report — "add a [bound/limit] check" — maps directly here: the missing invariant is "new remote host must equal old remote host," analogous to missing a price-limit bound on a swap.

This function is invoked from `repositoryWithRefreshedGitHubRepository` in `app-store.ts`, which is called as part of routine repository refresh, driven by `api.fetchRepository(owner, name)` against the account's configured `endpoint`. For GitHub Enterprise Server accounts (attacker-controlled or compromised endpoints the user has added), the JSON payload — including `clone_url` — is fully attacker-controlled, i.e., a "GitHub API object" per the report's threat model.

### Impact Explanation
If exploited, Desktop rewrites the user's trusted `origin` remote to point at an arbitrary host chosen by the attacker-controlled API endpoint, without any user confirmation or diff shown. Subsequent `git push`/`git fetch` operations (and their credentials, via `envForRemoteOperation`/trampoline askpass, see `app/src/lib/git/environment.ts` and `app/src/lib/trampoline/trampoline-credential-helper.ts`) are silently redirected to the attacker's server. This can result in code/credential exfiltration and corruption of what the user believes they are pushing to — mirroring the "smaller value for LPs instead of a smaller number" framing in the report (the number of remotes/behavior looks unchanged, but the destination's value/trust is corrupted).

### Likelihood Explanation
Medium-low. It requires the user to already have added an account for a GitHub Enterprise Server endpoint that is attacker-controlled or later compromised, and the target repository to already be tracked by Desktop with `gitHubRepository` association. There is no interaction required beyond Desktop's normal background repository refresh (`repositoryWithRefreshedGitHubRepository`), so once the precondition (malicious/compromised endpoint) is met, the rewrite happens automatically and silently.

### Recommendation
In `updateRemoteUrl`, in addition to `protocolsMatch`, require that the hostname of `updatedRemoteUrl` equals the hostname of the current `remoteUrl` before calling `setRemoteURL`, or surface the change to the user for explicit confirmation rather than applying it silently.

### Proof of Concept
1. User adds an account for GHE endpoint `https://ghe.evil-or-compromised.example`.
2. User clones/tracks a repo hosted there; Desktop stores `gitHubRepository.cloneURL = https://ghe.evil-or-compromised.example/acme/widgets.git` and configures `origin` to the same URL.
3. On a later background refresh, `api.fetchRepository('acme','widgets')` against that same endpoint returns a JSON payload with `clone_url: "https://attacker-server.example/acme/widgets.git"`.
4. `updateRemoteUrl` computes `protocolsMatch = true` (`https:` == `https:`), `remoteUrlUnchanged = true` (user hasn't manually edited remote), `urlsMatch = false` (different hostname) → it calls `setRemoteURL('origin', 'https://attacker-server.example/acme/widgets.git')`.
5. The next `git push`/`git fetch` performed by Desktop silently talks to `attacker-server.example`, exfiltrating pushed commits and any credentials configured for that operation. [2](#0-1)

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

**File:** app/src/lib/stores/app-store.ts (L4900-4907)
```typescript

      return repository
    }

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```
