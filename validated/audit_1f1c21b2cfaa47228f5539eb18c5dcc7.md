### Title
Automatic remote URL rewrite trusts unauthenticated `clone_url` from the GitHub API, allowing silent hijack of push/pull destination - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
GitHub Desktop periodically refreshes repository metadata from the GitHub API and, as a "helper" feature, will silently rewrite a repository's local `origin` remote URL when the API's `clone_url` no longer matches what the remote currently points to. This mirrors the `isAmmPair`-style configuration flaw in the report: the app applies a security-relevant change (its trusted list of "who to push/pull to") based on data it has not independently validated, with only superficial guardrails (protocol match) rather than any check that the new endpoint is actually the same trusted host/owner/repo.

### Finding Description
`updateRemoteUrl` compares the current `origin` remote URL against the `clone_url` returned by the GitHub API for the associated `GitHubRepository`, and if the remote hasn't been manually changed by the user (`remoteUrlUnchanged`) and the URL scheme is the same (`protocolsMatch`), it calls `gitStore.setRemoteURL` to overwrite the remote with whatever `clone_url` the API returned — with no validation that the new host/owner/name is related to the original repository at all: [1](#0-0) 

The only invariants enforced are:
- `protocolsMatch` — merely that both URLs use `http/https` vs `ssh`-style syntax, not that they point to the same host.
- `remoteUrlUnchanged` — that the *previous* API-known `cloneURL` still matches the current remote, which just proves the user hasn't manually re-pointed the remote; it says nothing about the trustworthiness of the *new* value.

There is no allow-list of trusted hosts (e.g., restricting rewrites to the same hostname as before, or to the account's configured GitHub.com/GHE endpoint), no confirmation prompt to the user, and no diffing of the actual repository identity (`owner`/`name`) before the URL is silently swapped in via `setRemoteURL`: [2](#0-1) 

`setRemoteURL` is invoked directly against the working repository's `origin`, meaning any subsequent `git push`/`git pull`/`git fetch` performed by the user through the normal UI will silently target whatever endpoint the API supplied, without any change being surfaced in the Repository Settings dialog unless the user manually opens it and notices the URL differs from what they expect. This is functionally identical to the `isAmmPair`-class bug: a single "current source of truth" for a security-relevant config value (`origin` URL) can be overwritten from untrusted/attacker-influenced input, without any allow-list, confirmation, or invariant check enforcing that swaps only happen among trusted values.

### Impact Explanation
If an attacker can influence the `clone_url` field returned for a `GitHubRepository` object — e.g. by compromising or MITM'ing a GitHub Enterprise Server instance, exploiting a malicious/compromised API response, or abusing a repository transfer/rename race — Desktop will automatically and silently rewrite the user's `origin` remote to point at an attacker-controlled Git server. Every future push from that user goes to the attacker's server instead of (or possibly in addition to information disclosure of) the real repository, and every future pull/fetch retrieves code from the attacker's server, which could then be silently merged/checked out and even pushed back. This satisfies the "silent corruption of what the user commits or pushes" impact class from an attacker-controlled "GitHub API object" as defined in the task's valid-impact scope.

### Likelihood Explanation
The code path executes automatically as part of routine repository refresh (whenever GitHub Desktop reconciles `GitHubRepository` metadata against the local git remote), requiring no unusual user action beyond normal use of the app while it periodically syncs repository metadata. The precondition is control over (or ability to influence) the JSON returned for a repository's `clone_url` — realistic for a compromised/malicious GHE server, a network position with a trusted-but-compromised enterprise endpoint, or any scenario where the API response is not fully trustworthy. This is a plausible, low-interaction path compared to the excluded categories (no local/physical access, no admin rights, no pre-existing malware, no leaked credentials, no social engineering beyond the app functioning as designed).

### Recommendation
Do not silently rewrite `origin` based solely on protocol-match/previous-URL-match heuristics. Require, at minimum:
- Hostname continuity checks against the account's configured endpoint (GitHub.com or the specific GHE host the user authenticated to) before allowing an automatic rewrite.
- A confirmation prompt to the user showing the old vs. new remote URL, similar to how `RepositorySettings` treats manual remote URL edits.
- Treating the previously-known-good remote URL as authoritative unless the user explicitly opts into or approves the change, rather than allowing an externally-sourced API field to unilaterally decide push/pull destinations.

### Proof of Concept
1. User has a repository cloned from `https://ghe.example.com/org/repo.git`, tracked as a `GitHubRepository` with `cloneURL = https://ghe.example.com/org/repo.git`.
2. Attacker compromises/MITMs the enterprise API endpoint (or otherwise causes the API response for that repository to be attacker-influenced) so that a subsequent `GET repos/org/repo` response returns `clone_url: https://attacker.example.com/org/repo.git`.
3. On the next repository metadata refresh, `updateRemoteUrl` sees: `protocolsMatch = true` (both `https:`), `remoteUrlUnchanged = true` (the existing remote still matches the last known-good `cloneURL`), and `urlsMatch(new clone_url, remote) = false`.
4. The guard at [3](#0-2)  is satisfied, and `gitStore.setRemoteURL('origin', 'https://attacker.example.com/org/repo.git')` is executed with no user confirmation.
5. The user's next `git push` (via Desktop's UI, unaware of the change) silently sends their commits to `attacker.example.com` instead of the real GHE server, and any subsequent fetch/pull silently pulls attacker-supplied content into the working tree.

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

**File:** app/src/lib/git/remote.ts (L1-1)
```typescript
import { git } from './core'
```
