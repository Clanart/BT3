### Title
Stale `gitHubRepository` association is never cleared, causing branch-protection checks and account/token resolution to silently operate on the wrong GitHub repository - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`VotingPowerProvider.unregisterOperatorVault` in the Sherlock report deletes a record from one mapping (`_operatorVaults`) but leaves a *derived* mapping (`_autoDeployedVault`) stale, so downstream logic keeps trusting outdated state. GitHub Desktop has the same structural defect: `Repository.gitHubRepository` is a cached, derived association between a local repo and a remote GitHub repo/account, and the code path responsible for refreshing it explicitly refuses to clear it when the remote no longer matches, per an acknowledged TODO: [1](#0-0) 

### Finding Description
`repositoryWithRefreshedGitHubRepository` is the only place that reconciles a `Repository`'s cached `gitHubRepository` (owner/name/endpoint/htmlURL/cloneURL) with the actual git remote. When `matchGitHubRepository` (hostname-only match against configured accounts) fails to find a match — e.g. because the remote was changed, removed, or now points to a non-GitHub/different host — the function bails out and returns the repository **unchanged**, keeping the old `gitHubRepository` object intact: [2](#0-1) 

This stale `gitHubRepository.endpoint` is subsequently used, without re-validating it against the current remote, to resolve which authenticated `Account` (and therefore which API token) to use for security-relevant operations:

- Branch protection lookups: `updateBranchProtectionsFromAPI` resolves the account purely from `repository.gitHubRepository.endpoint` and fetches `fetchProtectedBranches(owner.login, name)` for the (potentially stale) owner/name pair. [3](#0-2) 
- Account/token selection for AI features and commit-message generation: `getAccountForRepository` looks up the account solely by `gitHubRepository.endpoint`, with no cross-check against the live remote URL. [4](#0-3) 
- Notifications/checks: `notifications-store.ts` resolves the API account the same way, from the cached `GitHubRepository.endpoint`. [5](#0-4) 
- `withRefreshedGitHubRepository`/`getAccountForRepository` gate authenticated pull/fetch/push operations, again keyed off this same cached association. [6](#0-5) 

The `matchGitHubRepository` matcher itself only checks that the remote's hostname equals an account's endpoint hostname — it does not verify owner/name identity against the account's actual permissions before an association is (re)established, and once established it is asymmetric: it can be *set* but is documented as never being *cleared*: [7](#0-6) 

This is the same "unregister does not clean the derived mapping" pattern as the audit finding: `unregisterOperatorVault` removes the vault record but leaves `_autoDeployedVault[operator]` pointing at a vault the system no longer considers registered, so later reads (`getAutoDeployedVault`) and gating logic (`_registerOperatorImpl`) act on stale data. Here, the git remote is effectively "unregistered" (changed/removed) but `repository.gitHubRepository` is never invalidated, so every consumer that trusts it (branch protection, account resolution, AI feature account routing, notifications) keeps acting on a repository identity that no longer corresponds to the actual remote.

### Impact Explanation
Because a repository's remote configuration is attacker-influenceable content (an attacker with commit access to a shared repo, or one who gets a victim to point an existing local clone at a new remote — e.g. via a "helpful" setup script, a changed `git remote set-url` instruction in a README, or a proxy/mirror rewrite of `origin`), the stale association lets the app keep treating a repository as the *old* trusted GitHub repo:
- Branch-protection UI/behavior can be computed against the wrong owner/name, giving a false sense of safety (or restriction) for the repo actually being worked in.
- Token/account selection for AI-assisted commit messages and Copilot conflict resolution can silently route repository content to whichever account's endpoint happens to still be cached, rather than the account actually associated with the live remote — a data-exposure/account-binding confusion issue rather than a pure UX nuisance.
- The failure mode is silent: there is no user-facing indication that the association is stale, since the code path was designed to "bail early" rather than to reset the field.

This does not rise to remote code execution or direct token exfiltration, but it does match the report's accepted class of "silent corruption of trusted repository identity/state that downstream security decisions depend on."

### Likelihood Explanation
Moderate-to-low. It requires the local repository's remote to diverge from what `gitHubRepository` recorded (common after a fork migration, remote URL rewrite, or a compromised push changing collaboration URLs) and for the app to reach the `!match` branch of `repositoryWithRefreshedGitHubRepository`. This is a normal, frequently hit code path (`refreshSelectedRepositoryAfterAccountChange`, repository selection, sign-in/out) since it is invoked on ordinary account/repository refresh events, not a rare edge case: [8](#0-7) 

### Recommendation
When `matchGitHubRepository` returns no match (or matches a different owner/name than the currently cached `gitHubRepository`), explicitly clear/null out `repository.gitHubRepository` via `repoStore.setGitHubRepository(repository, null)` instead of returning the repository unchanged, mirroring the mitigation pattern from the audit report (proactively invalidate derived state on divergence rather than leaving it dangling). Additionally, `matchGitHubRepository`/`getAccountForRepository` should validate that the account's endpoint still corresponds to the *current* remote URL (not just a previously cached association) before it is used to authorize API calls for branch protection, checks, or AI features.

### Proof of Concept
Not executable from static analysis alone; the code-level reproduction is:
1. Open a repository in Desktop whose remote points to `github.com/owner/repo`, letting Desktop populate `repository.gitHubRepository` (owner/name/endpoint) via `repositoryWithRefreshedGitHubRepository`.
2. Change the repository's remote URL (e.g., `git remote set-url origin <new-url>`) to a URL that no longer resolves via `matchGitHubRepository` (e.g., a non-GitHub host, or a host with no configured account).
3. Trigger a refresh path that calls `repositoryWithRefreshedGitHubRepository` (e.g., repository selection or account change) — confirm via the code at `app/src/lib/stores/app-store.ts:4880-4885` that the stale `gitHubRepository` is returned unchanged.
4. Observe that `updateBranchProtectionsFromAPI`, `getAccountForRepository`, and notification/check-status code still use the old `gitHubRepository.endpoint`/owner/name for account resolution and API calls, despite the remote no longer pointing there.

I was not able to run the application to confirm the exact UI-visible consequence (e.g., whether a specific warning banner is suppressed); this assessment is based on static code paths only. Given the code contains an explicit acknowledged TODO/issue reference for this exact staleness (`https://github.com/desktop/desktop/issues/1144`), it is a known, long-standing limitation rather than a freshly discovered defect.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4874-4885)
```typescript
  private async repositoryWithRefreshedGitHubRepository(
    repository: Repository
  ): Promise<Repository> {
    const repoStore = this.repositoriesStore
    const match = await this.matchGitHubRepository(repository)

    // TODO: We currently never clear GitHub repository associations (see
    // https://github.com/desktop/desktop/issues/1144). So we can bail early at
    // this point.
    if (!match) {
      return repository
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

**File:** app/src/lib/stores/app-store.ts (L4935-4962)
```typescript
  private async updateBranchProtectionsFromAPI(repository: Repository) {
    if (repository.gitHubRepository === null) {
      return
    }

    const { owner, name } = repository.gitHubRepository

    const account = getAccountForEndpoint(
      this.accounts,
      repository.gitHubRepository.endpoint
    )

    if (account === null) {
      return
    }

    const api = API.fromAccount(account)

    const branches = await api.fetchProtectedBranches(owner.login, name)
    if (branches === null) {
      return
    }

    await this.repositoriesStore.updateBranchProtections(
      repository.gitHubRepository,
      branches
    )
  }
```

**File:** app/src/lib/stores/app-store.ts (L8285-8306)
```typescript
  private async withRefreshedGitHubRepository<T>(
    repository: Repository,
    fn: (repository: Repository) => Promise<T>
  ): Promise<T> {
    let updatedRepository = repository
    const account: Account | null = getAccountForRepository(
      this.accounts,
      updatedRepository
    )

    // If we don't have a user association, it might be because we haven't yet
    // tried to associate the repository with a GitHub repository, or that
    // association is out of date. So try again before we bail on providing an
    // authenticating user.
    if (!account) {
      updatedRepository = await this.repositoryWithRefreshedGitHubRepository(
        repository
      )
    }

    return fn(updatedRepository)
  }
```

**File:** app/src/lib/get-account-for-repository.ts (L10-21)
```typescript
/** Get the authenticated account for the repository. */
export function getAccountForRepository(
  accounts: ReadonlyArray<Account>,
  repository: Repository
): Account | null {
  const gitHubRepository = repository.gitHubRepository
  if (!gitHubRepository) {
    return null
  }

  return getAccountForEndpoint(accounts, gitHubRepository.endpoint)
}
```

**File:** app/src/lib/stores/notifications-store.ts (L496-511)
```typescript
  private async getAccountForRepository(repository: GitHubRepository) {
    const { endpoint } = repository

    const accounts = await this.accountsStore.getAll()
    return accounts.find(a => a.endpoint === endpoint) ?? null
  }

  private async getAPIForRepository(repository: GitHubRepository) {
    const account = await this.getAccountForRepository(repository)

    if (account === null) {
      return null
    }

    return API.fromAccount(account)
  }
```

**File:** app/src/lib/repository-matching.ts (L28-46)
```typescript
/** Try to use the list of users and a remote URL to guess a GitHub repository. */
export function matchGitHubRepository(
  accounts: ReadonlyArray<Account>,
  remote: string
): IMatchedGitHubRepository | null {
  for (const account of accounts) {
    const htmlURL = getHTMLURL(account.endpoint)
    const { hostname } = URL.parse(htmlURL)
    const parsedRemote = parseRemote(remote)

    if (parsedRemote !== null && hostname !== null) {
      if (parsedRemote.hostname.toLowerCase() === hostname.toLowerCase()) {
        return { name: parsedRemote.name, owner: parsedRemote.owner, account }
      }
    }
  }

  return null
}
```
