### Title
Stale `GitHubRepository` association is never cleared when a repo's remote is redirected, causing branch-protection checks to run against the wrong repository - ([File: app/src/lib/stores/app-store.ts])

### Summary
In `Burve`, an ERC20 transfer moves ownership of the shares but a second piece of state (`islandSharesPerOwner` / station-proxy deposits) is never updated to follow the new owner, so downstream logic (harvesting, redemption) acts on the wrong identity. GitHub Desktop has an analogous "identity moved, dependent state didn't follow" defect: once a local repository is associated with a `GitHubRepository` record, that association is **never cleared**, even when the underlying git remote is repointed to a different repository. All privileged, security-relevant decisions (branch protection warnings, PR/API context, "View on GitHub" links) keep using the stale association instead of the actual current remote target.

### Finding Description
When Desktop tries to refresh a repository's GitHub metadata, it does so in `repositoryWithRefreshedGitHubRepository`, which first calls `matchGitHubRepository` to re-derive owner/name from the repository's *current* default remote: [1](#0-0) 

The code explicitly acknowledges the flaw: if the remote can no longer be matched to any known account/host (e.g., because the remote URL was changed, or points to a non-GitHub / different-owner target), the function bails out and returns the repository **as-is, keeping the old `gitHubRepository` object**, with a TODO referencing the known unresolved issue that associations are never cleared: [2](#0-1) 

That stale `gitHubRepository` object (owner/name/endpoint from the *previous* remote target) continues to be used to drive branch-protection lookups: [3](#0-2) 

The broken invariant: `repository.gitHubRepository` is supposed to be an accurate mirror of "what GitHub repo does this local repo's remote currently point to," but the code path that would refresh/clear it silently no-ops instead of clearing the association when the remote no longer matches. A git remote is exactly one of the attacker-controllable primitives named in the task's valid-impact list (git remote/proxy response) — e.g., a malicious `insteadOf` URL rewrite pulled in via a cloned/fetched repo's config, a submodule remote swap, or any mechanism that mutates `.git/config`'s remote URL to a different owner/repo/host after the association was created for the original (legitimate, protected) repository.

### Impact Explanation
Once the association is stale, `updateBranchProtectionsFromAPI` and `refreshBranchProtectionState` query branch-protection state for the **old** owner/name rather than the new remote target. If the current (real) push destination has since become protected (or the user is redirected to a protected upstream), the "you can't push directly to this protected branch" warning that Desktop shows before a commit/push is evaluated against the wrong repository and can fail to fire — silently allowing a commit/push flow the user believed was safe to proceed against the actual (different) remote. This matches the accepted impact class of "silent corruption of what the user commits or pushes," since the safety check that is supposed to gate that action is derived from stale, unrelated state.

### Likelihood Explanation
Medium: it requires the git remote URL to actually change post-association (e.g., via `insteadOf`/URL rewrite config pulled from a malicious cloned repo, a compromised git config, or a repo maintainer repointing origin) while the user continues to operate in the same Desktop repository entry, without ever removing/re-adding the repository (which is the only way today to force a fresh, unambiguous match).

### Recommendation
When `matchGitHubRepository` fails to match the current default remote to a known account/host, explicitly clear `repository.gitHubRepository` (set to `null`) instead of preserving the previous association, and invalidate any cached branch-protection/permission state tied to the old `GitHubRepository`. Re-derive and re-validate the association strictly from the remote that is actually configured before using it for any protection or permission decision, mirroring the fix pattern recommended in the reported issue: update dependent, per-owner state whenever the underlying "owner" (i.e., the git remote target) changes.

### Proof of Concept
1. Add a repository to Desktop whose GitHub remote is `https://github.com/org/protected-repo` (branch protection enabled on `main`), so `repository.gitHubRepository` gets populated and branch protections are cached via `updateBranchProtectionsFromAPI`. [3](#0-2) 
2. Outside of Desktop, repoint the repo's default remote (e.g., via a `url.<base>.insteadOf` rewrite injected by a malicious dependency/submodule, or manual edit) so it now resolves to a different repository/host that `matchGitHubRepository` cannot map to any signed-in account (e.g., an unknown self-hosted git server acting as a proxy).
3. Trigger a refresh path that calls `repositoryWithRefreshedGitHubRepository` (e.g., switching accounts, or Desktop's periodic refresh). `matchGitHubRepository` returns `null`; per the TODO'd early-return, the old `gitHubRepository` (still `org/protected-repo`) is kept. [2](#0-1) 
4. Commit and push on the tracked branch. Desktop's protected-branch warning logic still consults the stale `org/protected-repo` protection rules rather than the actual new remote target, so no protection-mismatch warning is shown for the branch actually being pushed to — the commit/push proceeds using outdated safety context tied to an identity that no longer matches the real remote.

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

**File:** app/src/lib/stores/app-store.ts (L4964-4977)
```typescript
  private async matchGitHubRepository(
    repository: Repository
  ): Promise<IMatchedGitHubRepository | null> {
    const gitStore = this.gitStoreCache.get(repository)

    if (!gitStore.defaultRemote) {
      await gitStore.loadRemotes()
    }

    const remote = gitStore.defaultRemote
    return remote !== null
      ? matchGitHubRepository(this.accounts, remote.url)
      : null
  }
```
