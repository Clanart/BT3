## Analysis

The seed report's core pattern is: **a state transition removes the invariant that a previously-added guard depended on, but the guard keeps trusting the old, now-stale state to authorize a sensitive action** (crediting a formerly-safe borrower with an obligation nobody re-validated).

The closest Desktop analog is in `updateRemoteUrl`, which silently rewrites a repository's `origin` remote URL based on a **freshly-fetched GitHub API object**, using a **stale, previously-cached URL** as the sole "has the user customized this?" guard.### Title
Silent, unconfirmed rewrite of a trusted repository's `origin` remote URL from a GitHub API response - ([File: app/src/lib/stores/updates/update-remote-url.ts])

### Summary
`updateRemoteUrl` automatically rewrites a repository's `origin` remote to whatever `clone_url` the GitHub API returns for the associated `GitHubRepository`, gated only by a check that the *current* remote still matches the *previously cached* `cloneURL`. This "did the user customize it?" guard is the only thing standing between an attacker-influenced server-side value (`apiRepo.clone_url`) and a silent change to where the user's future pushes/fetches go — with no user confirmation dialog anywhere in the call path.

### Finding Description
`updateRemoteUrl` is invoked from `repositoryWithRefreshedGitHubRepository` [1](#0-0) , which itself runs automatically in background flows: after every push [2](#0-1) , after every pull [3](#0-2) , after every fetch [4](#0-3) , and whenever the active account changes [5](#0-4) .

The function's logic:
```
const remoteUrlUnchanged =
  gitStore.defaultRemote &&
  urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
  await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
}
``` [6](#0-5) 

The invariant being broken is exactly analogous to the seed report: a value (`gitHubRepository.cloneURL`) that was valid/trusted at the time it was cached is used later as the sole authorization check for a state-changing action (rewriting `origin`), without re-validating whether the *new* value being written is something the user actually consented to. The "credit limit" here is "the remote the user believes they are pushing/pulling to"; the transition that isn't re-guarded is "the API-reported clone URL diverges from what the user set up," and the guard (`remoteUrlUnchanged`) only checks whether the *old* cached value matches, not whether the *new* value is safe or user-approved.

`apiRepo` is server-controlled data obtained via `api.fetchRepository(owner, name)` [7](#0-6) , i.e., an attacker-influenced "GitHub API object" as defined in the impact criteria — this can be controlled by anyone with rename/transfer rights on the repository (an org admin, a compromised collaborator account, or a malicious/compromised GitHub Enterprise Server the victim is signed into), none of whom need any access to the victim's machine.

Once the remote is silently rewritten, the credential trampoline resolves credentials for the *new* host by origin match only: `findGitHubTrampolineAccount` matches an account to a URL purely by comparing `origin` (protocol+host), not the full repository path [8](#0-7) . So as long as the attacker points `clone_url` to a different repository under the *same* GitHub host, Desktop will still supply the victim's real token to git operations against that new target — with no prompt, no diff shown, no warning.

### Impact Explanation
This is a silent corruption of what the user pushes/fetches: a repository owner who is later demoted, an org admin, or a compromised/malicious GHES endpoint can retarget the victim's local `origin` to a different repository path on the same host. Subsequent pushes silently go to that different destination using the victim's own valid credentials (matched by host, not by path), redirecting or exfiltrating commits the user believed were going to their intended repository. Subsequent fetches/pulls could similarly pull objects from a location the user never explicitly agreed to. All of this happens with zero UI feedback — there is no dialog, banner, or diff review before `gitStore.setRemoteURL` executes.

### Likelihood Explanation
The rewrite path executes unconditionally as part of routine background repository refresh (after every push/pull/fetch and on account switch), requiring no special user action beyond normal use of the app. The only precondition is that the user hasn't manually customized `origin` away from the last-known API `cloneURL` — the common case for most users who never touch `git remote set-url`. The attacker precondition (control over what `clone_url` the API/server reports for the associated `GitHubRepository`) is realistic for a malicious/compromised org admin, a repo transfer, or a rogue/compromised GitHub Enterprise instance, all of which are unprivileged with respect to the victim's device.

### Recommendation
Do not silently rewrite `origin` (or any remote) based on API-reported `clone_url` changes. At minimum:
- Require explicit user confirmation before changing a configured remote URL, showing old vs. new URL.
- Re-validate that the new URL's owner/repo identity is consistent with what the user originally added (e.g., same repository ID, not just "old remote wasn't customized").
- Bind trampoline credential resolution to the specific repository being operated on, not just protocol+host origin, so a same-host redirect can't reuse account credentials transparently.

### Proof of Concept
1. Victim adds/clones a GitHub repository in Desktop; `origin` matches `gitHubRepository.cloneURL` exactly (no manual customization).
2. An attacker with admin/transfer rights on that repository (or control of the GHES instance backing the account) changes the reported `clone_url` for that repository — e.g., renames/transfers it, or a compromised/malicious enterprise server returns a different `clone_url` for the same repo id/owner/name.
3. Victim performs any push, pull, or fetch in Desktop, or simply switches accounts; `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl` runs automatically.
4. Because `remoteUrlUnchanged` still holds (comparing against the previously cached `cloneURL`) and `!urlsMatch` is true, Desktop silently calls `gitStore.setRemoteURL` to point `origin` at the attacker-supplied URL — no dialog is shown.
5. The next commit the victim pushes goes to the new, attacker-controlled destination using the victim's own token, since `findGitHubTrampolineAccount` supplies credentials based on host-origin match alone.

Note: I was unable to execute this end-to-end in a live environment (no runtime access); the analysis is based on static code review of the cited files, which show the guard logic, the automatic (non-user-initiated) call sites, and the host-only credential matching that compounds the impact.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4887-4890)
```typescript
    const { account, owner, name } = match
    const { endpoint } = account
    const api = API.fromAccount(account)
    const apiRepo = await api.fetchRepository(owner, name)
```

**File:** app/src/lib/stores/app-store.ts (L4904-4907)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L5340-5344)
```typescript
          // manually refresh branch protections after the push, to ensure
          // any new branch will immediately report as protected
          await this.refreshBranchProtectionState(repository)

          await this._refreshRepository(repository)
```

**File:** app/src/lib/stores/app-store.ts (L5602-5606)
```typescript
          // manually refresh branch protections after the push, to ensure
          // any new branch will immediately report as protected
          await this.refreshBranchProtectionState(repository)

          await this._refreshRepository(repository)
```

**File:** app/src/lib/stores/app-store.ts (L5973-5977)
```typescript
        // manually refresh branch protections after the push, to ensure
        // any new branch will immediately report as protected
        await this.refreshBranchProtectionState(repository)

        await this._refreshRepository(repository)
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L36-44)
```typescript
  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
```

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```
