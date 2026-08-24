### Title
Silent remote-URL takeover via unvalidated GitHub API `clone_url` in `updateRemoteUrl()` - (File: app/src/lib/stores/updates/update-remote-url.ts)

### Summary
The seed report describes an interface/contract mismatch: code trusts an external system's return value ("the interface says it returns X") without verifying the actual value, and that unchecked trust breaks downstream logic. The Desktop analog is `updateRemoteUrl()`, which trusts the `clone_url` field of a `GitHubRepository`/`IAPIRepository` object returned by the GitHub API and silently rewrites the local repository's `origin` remote to that value, validating only that the URL *scheme* ("https:" vs "ssh") matches — not that the host or path is legitimate.

### Finding Description
`updateRemoteUrl()` is invoked (via `withRefreshedGitHubRepository` in `app-store.ts`) whenever Desktop refreshes metadata for a repository from the GitHub API. It compares the current git remote URL to `apiRepo.clone_url`: [1](#0-0) 

The only safety checks performed are:
- `protocolsMatch`: both URLs parse to the same `URL.parse().protocol` (e.g., both `"https:"`).
- `remoteUrlUnchanged`: the *previous* API-known clone URL still matches the current remote — i.e., the user hasn't manually customized the remote.

Neither check validates that `updatedRemoteUrl`'s **hostname** or **path** correspond to the same GitHub repository, or to any trusted host at all. As long as the scheme is unchanged, `gitStore.setRemoteURL()` is called with whatever string the API/`GitHubRepository` object provided: [2](#0-1) 

`setRemoteURL` shells out to `git remote set-url` with the attacker-controlled string essentially unfiltered. This is the same class of bug as the report's seed: the code assumes an external interface (here, the GitHub API's repository object) returns a value with an implicit contract ("this clone_url always points at the same repository, just possibly a renamed one") that is never actually verified — much like the seed assumed `withdrawAllAndUnwrap()`'s return-type contract without checking the real behavior.

### Impact Explanation
`GitHubRepository` objects (and thus `clone_url`) originate from GitHub API responses, which are attacker-influenceable in the threat model given (e.g., a compromised/malicious GitHub Enterprise Server endpoint that the user has added an account for, or a spoofed/manipulated API response reaching the client). If an attacker can influence the `clone_url` field returned for a repository the user already has cloned, Desktop will silently re-point the user's `origin` remote to an attacker-controlled git server on the next repository refresh — without any user confirmation or diff-style warning. Subsequent `git push`/`git fetch` operations then interact with the attacker's server instead of the real one, enabling credential/token exfiltration during authenticated push/fetch (since Desktop's credential helper trampoline will supply GitHub credentials scoped to the *original* endpoint's host-matching heuristics) and enabling supply-chain corruption of what the user believes they are pushing to/pulling from.

### Likelihood Explanation
Exploitation requires the attacker-influenced repository metadata reach `updateRemoteUrl()` while `remoteUrlUnchanged` still holds (i.e., the user has not manually edited the remote) and the scheme stays the same (trivial to satisfy — just keep `https://`). This is a realistic condition for the common case of unmodified, freshly cloned repositories. The main enabling factor is a GitHub Enterprise Server (or any endpoint the user is signed into) able to return manipulated repository data — the "GitHub API object" attacker primitive explicitly listed as valid impact scope. Existing guards (`urlMatchesRemote`, `protocolsMatch`) check for *presence* of a scheme match, not hostname/repo identity, so they do not stop this path.

### Recommendation
Before silently applying `updatedRemoteUrl`, validate that the new URL's hostname matches an expected/trusted GitHub endpoint (the account's configured API endpoint) and that owner/name parse to a plausible rename of the same repository (e.g., compare `repo.id`/`node_id` rather than only URL strings), or otherwise require explicit user confirmation before rewriting `origin`.

### Proof of Concept
1. User has a repository cloned from a GitHub Enterprise Server instance and is signed in with an account for that endpoint.
2. The GHE instance (compromised, or an admin/attacker with write access to that API surface) returns a repository object for the same `id`/`node_id` but with `clone_url` pointing to `https://attacker-controlled-host/evil/evil.git`.
3. On the next repository metadata refresh (`withRefreshedGitHubRepository` → `updateRemoteUrl`), because the scheme is still `https:` and the remote hasn't been manually altered, `gitStore.setRemoteURL('origin', 'https://attacker-controlled-host/evil/evil.git')` executes silently.
4. All subsequent fetches/pushes from the affected repository go to the attacker's server.

Note: I was unable to fully trace all call sites of `withRefreshedGitHubRepository` in `app-store.ts` due to iteration limits, so the exact end-to-end trigger conditions (e.g., how often/whether this refresh runs automatically vs. on user action) should be confirmed by a deeper read of `app-store.ts` before treating this as fully validated.

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
