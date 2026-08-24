### Title
Automatic remote-URL repointing from unvalidated GitHub API `clone_url` bypasses user confirmation - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` silently repoints a repository's `origin` remote to whatever URL is present in `apiRepo.clone_url` — a value sourced from the GitHub/GHE API response — as soon as it decides "the remote looks unchanged from what the API previously reported." The only checks performed are string-structural (protocol match, owner/name comparison via `urlMatchesRemote`); there is no validation of `clone_url`'s well-formedness/scheme allow-list, and no user confirmation is ever surfaced before the local git config is mutated via `git remote set-url`.

### Finding Description
`updateRemoteUrl` in `app/src/lib/stores/updates/update-remote-url.ts:7-45` computes `updatedRemoteUrl = apiRepo.clone_url` directly from an `IAPIRepository` object obtained from the GitHub API, and — if the (previously observed) protocol matches and the current remote is unchanged from the last known API `cloneURL` — calls `gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)` unconditionally, with no dialog, prompt, or explicit user action: [1](#0-0) 

This mirrors the reported bug class exactly: a function that mutates a security-relevant mapping (`nativeToBridgedToken` in the Linea case; the git remote URL used for all future fetch/push here) based on caller/API-supplied data, guarded only by superficial checks (`onlyOwner` + `isNewToken` there; `protocolsMatch` + `urlMatchesRemote` structural check here), without validating that the new value is actually a safe/expected value and without any explicit confirmation step.

`setRemoteURL` itself performs no sanitization of the URL before shelling out: [2](#0-1) 

The only defense present in this code path is `urlMatchesRemote`, which merely compares parsed `hostname`/`owner`/`name` components: [3](#0-2) 

This does not validate the URL's scheme is one of `https`/`ssh`/`git`, nor that the hostname corresponds to a trusted GitHub/Enterprise host — `parseRemote` (`app/src/lib/remote-parsing.ts:55-64`) will happily parse and accept `clone_url` values pointing at attacker-controlled hosts as long as they fit the regex shapes, and if `clone_url` doesn't match any of the five accepted shapes, `urlMatchesRemote` returns `false`, which — combined with the `!urlsMatch` condition — is exactly the branch that triggers the silent `setRemoteURL` call: a poorly-formed or attacker-crafted `clone_url` is *more* likely to pass the "urls don't match" branch, not less.

### Impact Explanation
This is a "silent corruption of what the user commits or pushes" scenario as defined in-scope: an attacker who can influence the GitHub API response consumed here (e.g. a malicious or compromised GitHub Enterprise Server instance the Desktop client is configured against, or a man-in-the-middle/mirroring proxy sitting between Desktop and the GHE API) can cause Desktop to rewrite the user's `origin` remote to point at an attacker-controlled git host without any user awareness. Subsequent `git push`/`git fetch` operations initiated by the user through Desktop's normal UI would silently go to the attacker's server, allowing credential capture (since Desktop injects auth via the trampoline for whatever remote is configured) and/or serving malicious refs/objects back to the victim on next fetch.

### Likelihood Explanation
This requires an attacker-influenced GitHub API object (in scope per the task's Valid Impact definition), most plausibly for GitHub Enterprise Server deployments where the API host is not pinned to `github.com`, or via a compromised/rogue proxy fronting the API. It does not require local/physical access, admin rights, or existing malware — it only requires the attacker's API response to reach the client during a routine background repository refresh, which `updateRemoteUrl` participates in (called from `app-store.ts`, matches referenced 3 times).

### Recommendation
- Require that any automatically-applied `clone_url` update comes from the same trusted host as the account's configured endpoint before calling `setRemoteURL`.
- Do not silently rewrite remotes; surface a confirmation prompt to the user showing old vs. new URL before mutating git config, mirroring the fix pattern in the original report (reject/refuse rather than silently accept a value that wasn't explicitly authorized).
- Validate `clone_url`'s scheme against an allow-list (`https:`/`ssh:`/`git:`) and reject anything else before passing it into `git remote set-url`.

### Proof of Concept
1. User has a repository whose `origin` remote and associated `GitHubRepository.cloneURL` were previously in sync with a GitHub Enterprise Server instance.
2. Attacker compromises/mirrors the GHE API endpoint (or performs a MITM on the API path) and returns an `IAPIRepository` object whose `clone_url` is `https://evil-host/owner/name` while keeping the protocol scheme identical (`https:`).
3. On the next background refresh, `updateRemoteUrl` computes `protocolsMatch = true`, `remoteUrlUnchanged = true` (matches last-known API `cloneURL`), and `urlsMatch = false` (hostname differs) — the branch at `app/src/lib/stores/updates/update-remote-url.ts:42-44` fires and calls `gitStore.setRemoteURL(...)`, silently repointing `origin` to `https://evil-host/owner/name` with no user prompt.
4. Next time the user pushes/fetches via Desktop's UI, their traffic and credentials (via the trampoline) go to `evil-host`. [4](#0-3)

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
