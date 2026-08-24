### Title
Silent git remote URL rewrite from untrusted GitHub API data without hostname validation - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl()` mutates a repository's local `origin` remote URL based on the `clone_url` field returned by the GitHub API, but the guard that gates this write only compares URL **protocol strings** ("https:" == "https:"), never the **hostname**. If the API response is attacker-influenced (e.g. a malicious/compromised GitHub Enterprise Server the user has added an account for, or a MITM on an unencrypted/misconfigured GHES setup), Desktop will silently repoint the local git remote to an attacker-controlled host with no user confirmation, no diff shown, and no interaction required. This is a Checks-Effects-Interactions violation: the "interaction" (trusting external API data) happens before a sufficient "check" (verifying the new host is the expected one) is enforced, and the resulting "effect" (`git remote set-url`) silently corrupts where subsequent pushes/pulls/fetches go.

### Finding Description
`updateRemoteUrl()` is invoked automatically as part of background repository refresh logic, comparing the repository's current `origin` remote against the `clone_url` reported by the GitHub API for the linked `GitHubRepository`: [1](#0-0) 

The relevant checks are:
1. `protocolsMatch` — only verifies `URL.parse(remoteUrl).protocol === URL.parse(updatedRemoteUrl).protocol` (i.e., both are `"https:"`). It performs **no hostname comparison whatsoever**.
2. `remoteUrlUnchanged` — verifies the *current* remote still matches the previously *cached* `gitHubRepository.cloneURL` (via `urlMatchesRemote`, which does compare hostname/owner/name for that pair).
3. If both hold and the new URL differs from the current one, `gitStore.setRemoteURL(...)` is called immediately — an unconditional state mutation of the local git configuration, with no user prompt, diff, or confirmation dialog.

Critically, there is no check that `updatedRemoteUrl`'s hostname matches the *original* remote's hostname or the account's configured endpoint. `urlMatchesRemote` is used to validate the *old* pairing, but never to validate that the *new* URL still targets a trusted host: [2](#0-1) 

The `apiRepo` parameter that supplies `clone_url` ultimately comes from a GitHub API repository fetch (`fetchRepository`) tied to the account's configured endpoint: [3](#0-2) 

For accounts pointed at a GitHub Enterprise Server (a normal, unprivileged, user-controlled configuration in Desktop), the trust boundary for "the API response is honest" rests entirely on that server. A malicious or compromised GHES instance (or a MITM position on it) can return an arbitrary `clone_url` for a repository the victim already has cloned, and Desktop will accept it and rewrite `origin` to point anywhere, as long as the protocol string still says `https:`.

Once the remote is silently rewritten, all subsequent git network operations (`fetch`, `pull`, `push`) go to the attacker's host via the normal git codepaths (`app/src/lib/git/remote.ts`, `app/src/lib/git/environment.ts`), with no additional warning to the user.

### Impact Explanation
This falls into the explicitly valid impact category of "silent corruption of what the user commits or pushes": once `origin` is repointed, a user's `git push` will silently be delivered to the attacker's server instead of the legitimate one, while the UI gives no indication anything changed (no diff/confirmation was ever shown, since the rewrite happens as an automatic background side effect of a repository refresh). This can also enable social/technical follow-on attacks (e.g. serving a subsequent seemingly-legitimate "clone"/fetch from the attacker's server, feeding poisoned history back into the user's local checkout).

### Likelihood Explanation
Requires the account's configured GitHub endpoint (typically a GHES install) to be attacker-controlled or MITM-able — this fits the accepted "attacker controls...a GitHub API object" premise and does not require local/physical access, admin rights, or prior malware. It does require the repository refresh logic (`onGitStoreUpdated` → refresh flow) to have previously observed the "correct" `cloneURL` for the pairing (so `remoteUrlUnchanged` is true), which is the normal case for any freshly-added or steadily-used repository, making this reachable during ordinary background syncs rather than requiring unusual user action.

### Recommendation
In `updateRemoteUrl()`, before calling `gitStore.setRemoteURL`, validate that the new `clone_url`'s hostname matches the hostname of the existing remote (or the account's configured endpoint host) in addition to the protocol check — effectively require `urlMatchesRemote`-style hostname equality, not just owner/name equality, before ever mutating local git state. Consider also surfacing a confirmation prompt to the user before silently changing `origin`, consistent with the Checks-Effects-Interactions principle of completing all validation before performing the external-data-driven state change.

### Proof of Concept
1. Add a GitHub Enterprise account in Desktop pointed at a server the attacker controls (or can MITM), and add/clone a repository from it so that `gitHubRepository.cloneURL` is cached matching the current `origin` remote.
2. Have the malicious/compromised GHES server later return, for `GET repos/{owner}/{name}`, a JSON body whose `clone_url` field is `https://attacker-server.example/owner/name.git` (any hostname, as long as it still starts with `https://`).
3. Wait for Desktop's normal background repository refresh to call `updateRemoteUrl(gitStore, gitHubRepository, apiRepo)`: [4](#0-3) 
4. Since `protocolsMatch` is true (both `https:`) and `remoteUrlUnchanged` is true (nothing was manually changed locally), Desktop calls `gitStore.setRemoteURL('origin', 'https://attacker-server.example/owner/name.git')` with no user interaction.
5. The next `git push`/`fetch`/`pull` performed by the user silently targets `attacker-server.example` instead of the legitimate repository.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-45)
```typescript
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

**File:** app/src/lib/repository-matching.ts (L90-117)
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
```

**File:** app/src/lib/api.ts (L972-988)
```typescript
  /** Fetch a repo by its owner and name. */
  public async fetchRepository(
    owner: string,
    name: string
  ): Promise<IAPIFullRepository | null> {
    try {
      const response = await this.ghRequest('GET', `repos/${owner}/${name}`)
      if (response.status === HttpStatusCode.NotFound) {
        log.warn(`fetchRepository: '${owner}/${name}' returned a 404`)
        return null
      }
      return await parsedResponse<IAPIFullRepository>(response)
    } catch (e) {
      log.warn(`fetchRepository: an error occurred for '${owner}/${name}'`, e)
      return null
    }
  }
```
