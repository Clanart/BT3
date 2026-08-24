### Title
Silent Auto-Rewrite of Local Git Remote URL From an Untrusted GitHub API Response Can Redirect Fetch/Push to an Attacker-Controlled Repository - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

### Summary
`updateRemoteUrl` automatically calls `gitStore.setRemoteURL(...)` to rewrite the local `origin` remote in `.git/config` whenever the GitHub API's `clone_url` for the repository differs from the currently configured remote, with no user confirmation. The only guards are that the git protocol scheme (`https:`/`ssh:`) stays the same and that the user hasn't manually edited the remote since Desktop last learned the repository's `cloneURL`. There is no check that the new `clone_url`'s owner/repo is the "same" repository the user originally trusted (e.g., verifying it's a rename of the same underlying `id`) — it simply trusts whatever `IAPIRepository.clone_url` the API returned.

### Finding Description
The vulnerable logic: [1](#0-0) 

Key facts:
- `remoteUrl`/`updatedRemoteUrl` are compared via `urlMatchesRemote`, which only checks hostname/owner/name equality between two URL strings — it never validates that the *new* owner/name corresponds to a repository the user actually intended to keep using.
- `protocolsMatch` only checks that the URL scheme (`https:` vs `ssh:`) is unchanged — it does not check the hostname.
- `remoteUrlUnchanged` only verifies the user hasn't manually retargeted their remote away from the last-known `cloneURL` — it does not validate the new value's trustworthiness.
- If those three conditions hold, Desktop silently calls `gitStore.setRemoteURL` and rewrites the `origin` URL in the user's local Git config with **no dialog, diff, or confirmation** shown to the user.

This function is invoked from `repositoryWithRefreshedGitHubRepository`, which fetches the "fresh" `apiRepo` via `API.fromAccount(account).fetchRepository(owner, name)` and feeds its `clone_url` straight into `updateRemoteUrl`: [2](#0-1) 

`repositoryWithRefreshedGitHubRepository` runs during routine app flows (e.g. account sign-in/out refresh via `refreshSelectedRepositoryAfterAccountChange`), meaning the write happens automatically in the background without any explicit user action: [3](#0-2) 

**Broken invariant:** the trust boundary assumed by `updateRemoteUrl` is "the GitHub API response for this repository always reflects the user's legitimate, intended remote." But the `clone_url` field is attacker-influenceable: a compromised/malicious GitHub Enterprise Server instance, a MITM'd/rogue proxy sitting between Desktop and the API host (explicitly in-scope per the report's "remote/proxy response" category), or a manipulated API object can return an arbitrary `clone_url` (any owner/name on the same host, since only protocol — not hostname — is checked to be unchanged). Desktop will silently accept it and rewrite the local remote to point at that attacker-chosen destination.

This mirrors the Sherlock report's core pattern: an authorization/trust decision (`callbackAuthorized[market.owner]` staying valid across an ownership transfer) is made against stale/mutable external state without re-validating the new value, silently breaking (or here, silently corrupting) the operation for the legitimate party. In BondBaseSDA, an unvalidated `newOwner_` broke the market; here, an unvalidated `clone_url` corrupts the remote a user unknowingly pushes to and fetches from.

### Impact Explanation
If `origin` is silently repointed to an attacker-controlled repository under the same host:
- **Push corruption**: subsequent `git push` operations from the victim (using their own valid, cached GitHub token via the credential trampoline, matched purely by hostname — see `findGitHubTrampolineAccount`) will silently send the user's commits/branches to the attacker's repository instead of the intended one. This is "silent corruption of what the user commits or pushes," an explicitly in-scope impact.
- **Fetch/pull poisoning**: subsequent fetches will pull code from the attacker-controlled repository and merge/rebase it into the user's working tree, potentially achieving code execution via build scripts, hooks, or IDE auto-run, without the user ever noticing the remote changed (Repository Settings UI shows the URL, but there is no proactive warning).
- Because the credential/trampoline account matching (`findGitHubTrampolineAccount`) keys only on endpoint hostname, not on `owner/name`, the victim's legitimate token will be transparently supplied to authenticate against the attacker's repo, so no credential prompt reveals the switch.

### Likelihood Explanation
This path requires the attacker to control (or corrupt) a GitHub API response/proxy in the request chain to `api.fetchRepository`, which is explicitly listed as an acceptable primitive ("a GitHub API object... or a git remote/proxy response"). It does not require local access, admin rights, or social engineering. The trigger (`repositoryWithRefreshedGitHubRepository`) runs during routine, unprompted background refreshes (e.g., on account changes), so no unnatural user interaction is needed beyond normal use of Desktop against a compromised GHES/proxy or an API man-in-the-middle. The lack of hostname-change protection and lack of any user confirmation before writing to `.git/config` make the guard insufficient once the attacker can influence the API payload.

### Recommendation
Before calling `gitStore.setRemoteURL` in `updateRemoteUrl`:
- Require the new `clone_url`'s hostname to match the existing remote's hostname (not just protocol scheme).
- Require confirmation that the API-reported repository is the *same* underlying repository (e.g. compare a stable `id`/`node_id` rather than trusting the `clone_url` string wholesale) before treating a mismatch as a legitimate rename.
- Surface a user-facing confirmation dialog (similar to the existing `UpstreamAlreadyExists` dialog pattern) any time Desktop is about to auto-rewrite a remote URL, rather than performing the write silently.

### Proof of Concept
1. Victim has a repository cloned from `https://github.com/victim/legit-repo` and signed in with a GitHub.com account.
2. Attacker compromises or intercepts (MITM/rogue proxy) the network path used by `API.fromAccount(account).fetchRepository('victim','legit-repo')`, returning a crafted `IAPIFullRepository` object where `clone_url` is `https://github.com/attacker/legit-repo-clone` (attacker's own repo, valid HTTPS on github.com, so `protocolsMatch` passes).
3. A routine refresh occurs (e.g. `refreshSelectedRepositoryAfterAccountChange` triggered by any sign-in/out event) invoking `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`: [4](#0-3) 
4. Because `remoteUrlUnchanged` is true (the user hasn't manually edited their remote) and `urlsMatch` is false (URLs differ), `gitStore.setRemoteURL('origin', 'https://github.com/attacker/legit-repo-clone')` runs silently: [5](#0-4) 
5. The user, unaware, continues committing and clicks "Push origin" in Desktop; their commits and the victim's valid GitHub token (matched purely by hostname via `findGitHubTrampolineAccount`) are sent to the attacker's repository instead of the intended one.

**Uncertainty**: I could not fully trace every code path that could inject a malicious `IAPIFullRepository`/`clone_url` into this flow (e.g., whether GHE certificate/proxy validation elsewhere in the app would block a MITM before reaching `fetchRepository`), since the index doesn't cover the full HTTP/TLS pinning logic in `api.ts`. If Desktop enforces strict certificate validation for all GitHub.com/GHE requests with no configurable proxy bypass, the attacker's practical entry point would be narrower (limited to compromised/malicious GHE instances or configured HTTP(S) proxies), which the report's scope explicitly still allows ("a git remote/proxy response").

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

**File:** app/src/lib/stores/app-store.ts (L4886-4908)
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

**File:** app/src/lib/stores/app-store.ts (L4916-4933)
```typescript
  /**
   * Refreshes the GitHub repository information for the currently selected
   * repository when the active account changes. This ensures that permission
   * information is updated after signing in/out.
   */
  private async refreshSelectedRepositoryAfterAccountChange() {
    const repository = this.selectedRepository

    if (repository === null || repository instanceof CloningRepository) {
      return
    }

    if (!isRepositoryWithGitHubRepository(repository)) {
      return
    }

    await this.repositoryWithRefreshedGitHubRepository(repository)
  }
```
