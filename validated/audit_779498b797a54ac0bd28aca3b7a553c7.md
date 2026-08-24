### Title
Automatic rewrite of git remote URL from unauthenticated GitHub API `clone_url` data - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
GitHub Desktop silently rewrites a repository's `origin` remote URL whenever it detects that the GitHub API's `clone_url` for the associated `GitHubRepository` differs from the locally configured remote, using only same-owner/name-heuristics as a gate rather than requiring explicit user confirmation. This mirrors the Stargate report's underlying pattern — a "self-updating trusted address" invariant (there: the Stargate router address; here: the git remote URL) that is rewritten automatically based on external, non-user-supplied input, with the only protection being a set of heuristic string comparisons rather than an explicit trust boundary check.

### Finding Description
`updateRemoteUrl` unconditionally calls `gitStore.setRemoteURL()` — which runs `git remote set-url` — whenever three conditions are met: the protocols match, the *previously cached* `GitHubRepository.cloneURL` still matches the current remote, and the *new* `apiRepo.clone_url` from the API no longer matches the current remote: [1](#0-0) 

The "safety" checks (`urlMatchesRemote`, `urlsMatch`) only compare `hostname` + `owner` + `name` extracted via regex from `parseRemote`/`parseRepositoryIdentifier` — they do not verify that the new URL still points to the same trusted host/account: [2](#0-1) 

Because the value being written into the user's local git configuration (`.git/config`'s `remote.origin.url`) originates entirely from an `IAPIRepository` object fetched over the network — an object the task's threat model explicitly allows an attacker to control (a malicious/compromised GHES server, or a GitHub API response tampered with via a MITM on a misconfigured/legacy TLS setup) — a server that returns a crafted `clone_url` with the *same owner/name* but a *different, attacker-controlled hostname or credential-embedded URL* would pass `protocolsMatch` and `remoteUrlUnchanged`, and be written directly into the repository's remote without any user prompt or diff/confirmation UI.

### Impact Explanation
If the attacker-controlled `clone_url` is written to `origin`, the very next `push` sends the user's code (potentially private/proprietary) to the attacker's server instead of the real one — this is squarely "silent corruption of what the user commits or pushes," one of the explicitly valid impact categories. Because HTTPS git remotes can also embed credentials (`https://user:token@host/...`), or the URL can point to a host for which Desktop's credential-provider logic (`findGitHubTrampolineAccount`, `envForRemoteOperation`) would then supply the currently signed-in account's token over the new host, this can also lead to credential exfiltration during the next fetch/push if the new host captures the Basic-Auth/token handshake.

### Likelihood Explanation
Likelihood is difficult to fully assess from static code alone. Triggering this path requires a specific sequence: the user must have an existing `GitHubRepository` record with a previously matching `cloneURL`, and then the API response for that repo (from `/repos/{owner}/{name}`) must return an `IAPIRepository.clone_url` that differs in a way that still passes the owner/name-based `urlsMatch`. This means a real compromise or malicious GHES/GHE server is needed to serve this crafted response — legitimate GitHub.com API responses would not naturally produce owner/name-matching but host-differing `clone_url` values under normal operation (this normally only fires for legitimate repo renames/transfers, where hostname stays the same). I was not able to trace every caller of `updateRemoteUrl` in `app-store.ts` in this session, so I cannot confirm all the trigger conditions (e.g., whether it runs on every background repository refresh or requires a specific API endpoint), which limits full verification of exploit preconditions.

### Recommendation
Do not automatically call `git remote set-url` based on unauthenticated API data. At minimum:
- Require the new URL's hostname to exactly match the previous remote/account's endpoint hostname before auto-rewriting (not just owner/name).
- Surface the proposed remote URL change to the user for explicit confirmation instead of applying it silently.
- Treat `apiRepo.clone_url` as untrusted input and validate it against the known/expected endpoint associated with the signed-in `Account` before use in `updateRemoteUrl`.

### Proof of Concept
Conceptual PoC (not verified end-to-end due to inability to trace all callers in this session):
1. Attacker controls or MITMs a GitHub Enterprise Server response for `GET /api/v3/repos/{owner}/{repo}` that the victim's Desktop client queries as part of its normal repository refresh.
2. The attacker's response returns the *same* `owner`/`repo` name (so `urlsMatch`/`urlMatchesRemote` pass) but a `clone_url` pointing to an attacker-controlled host serving the same path structure, e.g. `https://attacker-mirror.example/{owner}/{repo}.git`.
3. `updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts` sees `protocolsMatch === true`, `remoteUrlUnchanged === true` (against the cached `GitHubRepository.cloneURL`), `urlsMatch === false` (against the new `clone_url` vs. current git remote) — satisfying `protocolsMatch && remoteUrlUnchanged && !urlsMatch` — and calls `gitStore.setRemoteURL('origin', attackerURL)`.
4. The victim's next `git push` silently sends commits to the attacker's host. [3](#0-2)

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
