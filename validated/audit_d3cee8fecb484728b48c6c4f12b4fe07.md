### Title
Silent, unconfirmed rewrite of a repository's git remote URL from GitHub API data - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
The external report's core issue is that a security-relevant parameter (`A`, controlling the pricing curve) is changed abruptly, in a single step, based on trusted input, with no confirmation/timelock to let affected parties react. The closest analog in GitHub Desktop is `updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts`, which rewrites a local repository's `origin` remote URL — a value that controls where future `fetch`/`push` operations (and the credentials sent with them) go — automatically and silently, based solely on the `clone_url` field of a `GitHubRepository`/`IAPIRepository` object, with no user prompt or review step.

### Finding Description
`updateRemoteUrl` compares the current default remote URL against `apiRepo.clone_url` and, if the protocol matches and the URL differs from what Desktop last cached, calls `gitStore.setRemoteURL` to rewrite the remote in a single, un-confirmed step: [1](#0-0) 

This eventually invokes `git remote set-url` directly: [2](#0-1) 

The only checks performed are:
- protocol equality between old and new URL (`protocolsMatch`)
- that the *previously cached* clone URL from the GitHub API still matches the current remote (`remoteUrlUnchanged`), which only guards against the user manually customizing the remote, not against a malicious/incorrect `clone_url` value
- that the new URL doesn't already match (`urlsMatch`)

There is no check that the new hostname is the same trusted host, no confirmation UI, and no way for the user to review or reject the change before it is applied — the same "abrupt parameter change with no timelock/opt-out" pattern flagged in the external report, just applied to the remote URL instead of a curve parameter `A`.

### Impact Explanation
The value being silently corrupted is the git `origin` remote URL, which determines the destination of all subsequent `git push`/`git fetch`/`git pull` traffic and, for HTTPS remotes, the host to which the OS/Desktop credential helper will present the user's stored GitHub token. If the `IAPIRepository`/`GitHubRepository.cloneURL` value driving this function can be influenced by an untrusted source (e.g., an Enterprise/self-hosted GitHub API endpoint returning attacker-controlled JSON, or a repository object obtained via `open-repository-from-url` / `oauth` deep-link flows before the local repository model is fully validated), Desktop would rewrite the remote to an attacker URL with no user awareness. Subsequent pushes/fetches would silently go to the wrong host, and HTTPS credential exchange could leak the token to that host.

### Likelihood Explanation
This is lower likelihood than a typical "attacker controls fetched repo/API object" primitive because: (1) `updateRemoteUrl` is gated behind matching an already-known `GitHubRepository` record tied to a specific repository ID, so a fully external attacker cannot trivially inject an arbitrary `clone_url` for a repo the user didn't already associate; (2) `protocolsMatch` and `remoteUrlUnchanged` narrow the window further. The realistic path is a legitimate repository rename/transfer race combined with a compromised or spoofed Enterprise API response — a narrower condition than a generic "any GitHub API object" attacker, and I could not fully trace, within the available index, every call site (`app-store.ts` matches were truncated by the index) that feeds `apiRepo` into `updateRemoteUrl`, so I cannot confirm with certainty that fully untrusted input reaches this function without additional validation upstream.

### Recommendation
Require explicit user confirmation (a dialog, similar to other destructive/identity-changing actions in Desktop) before rewriting an existing remote URL in response to API-derived data, rather than performing the `git remote set-url` automatically. At minimum, validate that the new URL's hostname matches the account's configured endpoint before calling `setRemoteURL`, and log/surface the change prominently so users can detect unexpected remote rewrites — mirroring the report's recommendation of a "timelock"/gradual, reviewable mechanism instead of an instantaneous unattended change.

### Proof of Concept
Could not be fully constructed from the indexed code alone: reproducing this requires tracing the exact call sites in `app-store.ts` where `apiRepo`/`GitHubRepository` records are refreshed and fed into `updateRemoteUrl` (the relevant lines were not available in the index due to size limits), to confirm whether any of those refresh paths can be triggered by data an external actor controls (e.g., a malicious GitHub Enterprise Server response, or a crafted `open-repository-from-url` deep link that associates a new `GitHubRepository` before the remote is validated). I recommend a Devin session with full repository access to trace these call sites (`app-store.ts`, search for `updateRemoteUrl(` usages) and confirm/deny reachability from an unprivileged, attacker-influenced input before treating this as a confirmed exploitable finding. [3](#0-2) [4](#0-3)

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
