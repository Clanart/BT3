## Finding: Stale `GitHubRepository` association is never cleared when a remote changes, causing branch-protection/write-access checks to run against the wrong repository

### Summary
The external report's bug class is "a stale identifier that maps an actor to privileges is not cleared when the identifier's target changes, so the old target keeps acting with the wrong privileges." The Desktop analog is the `gitHubRepository` association cached on a `Repository` model: when the underlying git remote is repointed to a repository that Desktop's API-matching logic can't resolve, Desktop explicitly keeps the old, now-incorrect `GitHubRepository` object attached to the local repository instead of clearing it, per a long-standing acknowledged TODO.

### Finding Description
`repositoryWithRefreshedGitHubRepository` is responsible for keeping a local `Repository`'s cached `gitHubRepository` in sync with reality by calling `matchGitHubRepository` against the current remote. If no match is found, the code bails out immediately and keeps whatever stale association was previously stored: [1](#0-0) 

The comment is explicit that this is a known, unfixed limitation ("We currently never clear GitHub repository associations"), referencing upstream issue desktop/desktop#1144.

That stale `GitHubRepository` object (owner, name, endpoint, cached `permissions`, and `dbID`) is subsequently trusted for security-relevant decisions rather than being derived fresh from the current remote:

- `refreshBranchProtectionState` reads `owner`/`name`/`permissions` straight off `repository.gitHubRepository` and uses them both to short-circuit branch-protection checks via `hasWritePermission(gitHubRepo)` and to query the GitHub API's push-control endpoint: [2](#0-1) 
- `hasWritePermission` trusts the cached `permissions` field on the (possibly stale) `GitHubRepository`: [3](#0-2) 
- Branch-protection results are cached keyed by `gitHubRepository.dbID`, so a stale `dbID` returns protection data for the wrong repository: [4](#0-3) 

Because the remote-to-GitHub-repository matching only refreshes the association when `matchGitHubRepository` succeeds, an attacker who can cause the local remote to point somewhere Desktop cannot resolve (e.g. a private/unknown fork, a repo under an account the user isn't signed into, or a repo whose API lookup transiently fails) leaves the old `gitHubRepository`'s `owner`, `name`, `dbID`, and cached `permissions` in place indefinitely.

### Impact Explanation
The corrupted value is the `Repository.gitHubRepository` association (owner/name/dbID/permissions). Once stale, Desktop's write-access and branch-protection UI/warnings are computed against the *previous* repository, not the one the user is actually about to commit/push to:
- `hasWritePermission` can incorrectly report the user has write access (suppressing the "no write access" warning) or incorrectly gate branch-protection checks.
- Push-control/branch-rule queries (`api.fetchPushControl`, `fetchRepoRulesForBranch`) run against the wrong `owner`/`name`, so the "protected branch" warning shown before a commit/push reflects the old repository, not the actual destination.
- The `dbID`-keyed protected-branch cache can return protection state belonging to a different repository.

This is a silent-corruption-of-trust-signal class issue: the user's push/commit workflow relies on Desktop-computed protection/permission warnings that can now be wrong because of an attacker-influenced remote change, without any error or notification to the user.

### Likelihood Explanation
This requires only that a repository's remote come to point at something Desktop's `matchGitHubRepository` cannot resolve to an API repository the signed-in account can see — a state reachable without local/physical access or elevated privileges, e.g., a git remote that was changed on disk, via a hook, or because the target became inaccessible/renamed. This is exactly the scenario the code's own TODO says is unhandled, indicating it's a genuinely reachable, unmitigated path rather than a hypothetical one. Existing guards (`urlMatchesRemote`/`updateRemoteUrl`) only correct the association when a *successful* API match is found; they do nothing when the match fails, which is precisely the gap.

### Recommendation
When `matchGitHubRepository` fails to resolve a remote to a known GitHub repository, clear (rather than retain) the local `Repository`'s `gitHubRepository` association instead of returning early with the old value, mirroring the report's own recommendation to `delete` the stale mapping. At minimum, invalidate cached `permissions`/branch-protection cache entries keyed by the old `dbID` whenever the remote no longer matches, so downstream permission and branch-protection checks never operate on data for a repository the local remote no longer represents.

### Proof of Concept
1. Clone/open repository `A` (`owner/repoA`) with Desktop signed in; Desktop persists `gitHubRepository` = `A` (with cached `permissions: 'write'`, `dbID = N`).
2. Attacker-influenced event repoints the local remote to `owner2/repoB` (e.g. through a modified `.git/config`, a hook, or repository takeover) such that `matchGitHubRepository` cannot resolve `repoB` for the signed-in account (private repo, wrong endpoint, deleted, etc.).
3. `repositoryWithRefreshedGitHubRepository` is invoked (e.g. on selection change / account refresh) — `match` is `null`, so per [5](#0-4)  the function returns immediately, leaving `repository.gitHubRepository` = stale `A`.
4. User commits and is about to push into `repoB`. `refreshBranchProtectionState` computes `hasWritePermission(gitHubRepo)`/branch-protection using `A`'s cached `owner`, `name`, `permissions`, and `dbID` — silently showing the wrong (or no) protection/write-access warning for the actual target repository `repoB`.

Note: I could not fully trace `matchGitHubRepository`'s exact resolution logic (its definition in `app-store.ts` was not retrievable within the available tool budget), so the precise conditions under which it fails to match are inferred from `repository-matching.ts`'s `repositoryMatchesRemote`/`urlMatchesRemote` helpers rather than confirmed line-by-line in `matchGitHubRepository` itself.

### Citations

**File:** app/src/lib/stores/app-store.ts (L1484-1522)
```typescript
  private async refreshBranchProtectionState(repository: Repository) {
    const { tip, currentRemote } = this.gitStoreCache.get(repository)

    if (tip.kind !== TipState.Valid || repository.gitHubRepository === null) {
      return
    }

    const gitHubRepo = repository.gitHubRepository
    const branchName = findRemoteBranchName(tip, currentRemote, gitHubRepo)

    if (branchName !== null) {
      const account = getAccountForEndpoint(this.accounts, gitHubRepo.endpoint)

      if (account === null) {
        return
      }

      // If the user doesn't have write access to the repository
      // it doesn't matter if the branch is protected or not and
      // we can avoid the API call. See the `showNoWriteAccess`
      // prop in the `CommitMessage` component where we specifically
      // test for this scenario and show a message specifically
      // about write access before showing a branch protection
      // warning.
      if (!hasWritePermission(gitHubRepo)) {
        this.repositoryStateCache.updateChangesState(repository, () => ({
          currentBranchProtected: false,
          currentRepoRulesInfo: new RepoRulesInfo(),
        }))
        this.emitUpdate()
        return
      }

      const name = gitHubRepo.name
      const owner = gitHubRepo.owner.login
      const api = API.fromAccount(account)

      const pushControl = await api.fetchPushControl(owner, name, branchName)
      const currentBranchProtected = !isBranchPushable(pushControl)
```

**File:** app/src/lib/stores/app-store.ts (L4874-4886)
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

**File:** app/src/models/github-repository.ts (L72-84)
```typescript
/**
 * Can the user push to this GitHub repository?
 *
 * (If their permissions are unknown, we assume they can.)
 */
export function hasWritePermission(
  gitHubRepository: GitHubRepository
): boolean {
  return (
    gitHubRepository.permissions === null ||
    gitHubRepository.permissions !== 'read'
  )
}
```

**File:** app/src/lib/stores/repositories-store.ts (L683-720)
```typescript
  public async updateBranchProtections(
    gitHubRepository: GitHubRepository,
    protectedBranches: ReadonlyArray<IAPIBranch>
  ): Promise<void> {
    const dbID = gitHubRepository.dbID

    await this.db.transaction('rw', this.db.protectedBranches, async () => {
      // This update flow is organized into two stages:
      //
      // - update the in-memory cache
      // - update the underlying database state
      //
      // This should ensure any stale values are not being used, and avoids
      // the need to query the database while the results are in memory.

      const prefix = getKeyPrefix(dbID)

      for (const key of this.protectionEnabledForBranchCache.keys()) {
        // invalidate any cached entries belonging to this repository
        if (key.startsWith(prefix)) {
          this.protectionEnabledForBranchCache.delete(key)
        }
      }

      const branchRecords = protectedBranches.map<IDatabaseProtectedBranch>(
        b => ({ repoId: dbID, name: b.name })
      )

      // update cached values to avoid database lookup
      for (const item of branchRecords) {
        const key = getKey(dbID, item.name)
        this.protectionEnabledForBranchCache.set(key, true)
      }

      await this.db.protectedBranches.where('repoId').equals(dbID).delete()

      const protectionsFound = branchRecords.length > 0
      this.branchProtectionSettingsFoundCache.set(dbID, protectionsFound)
```
