## Title
Stale `GitHubRepository`/account association survives repository content replacement, causing Desktop to apply the wrong account/fork-target trust to a new (attacker) repository at a reused path - (File: `app/src/lib/stores/repositories-store.ts`)

### Summary
The Sherlock report's root cause is that a resource's ownership/content can change (veNFT `receiptID` transfer) while a privileged pointer (`managerAddress`) tied to that resource is never re-derived, so the old, now-unrelated controller keeps privileged access. GitHub Desktop has the same class of bug: the `Repository` record that binds a local working-directory `path` to a `GitHubRepository` (and thus to an authenticated `Account`, cached branch-protection state, PR list, and fork-contribution target) is keyed purely by filesystem `path` and is never invalidated when the content at that path changes to a different, attacker-controlled repository.

### Finding Description
`RepositoriesStore.addRepository()` looks up an existing DB row purely by `path` and, if found, returns it unchanged instead of re-deriving its GitHub association: [1](#0-0) 

`matchExistingRepository()` used across the app (`app-store.ts`, `dispatcher.ts`, `app.tsx`) also matches solely on normalized `path`: [2](#0-1) 

Crucially, Desktop explicitly documents (and never fixes) that GitHub repository associations are never cleared: [3](#0-2) 

That stale `GitHubRepository` association is what determines the `Account` used for authenticated operations: [4](#0-3) 

It is also what determines the fork/parent push target (`getNonForkGitHubRepository`) that governs where PRs/branch defaults/autocompletion point, and it feeds per-`GitHubRepository`-`dbID` caches (branch protection, PR list) in `RepositoriesStore`/`PullRequestCoordinator` that are shared across every local `Repository` row with the same `gitHubRepositoryID`: [5](#0-4) [6](#0-5) 

**Attack path:** A victim has a legitimate repository tracked by Desktop at local path `P`, associated in the Desktop DB with `GitHubRepository` X and their own `Account`. The victim later deletes the folder at `P` and adds/clones a different, attacker-supplied repository into the same path `P` (e.g. via "Add existing repository", drag-and-drop, or "Clone Again" after the folder went missing — see `MissingRepository.cloneAgain` which reuses `repository.path`): [7](#0-6) 

Because `addRepository`/`matchExistingRepository` key exclusively on `path`, Desktop reattaches the brand-new (attacker) content to the *old* DB row, keeping the old `gitHubRepositoryID`, cached fork-contribution target, and branch-protection/PR caches — none of which are re-validated against the new remote content. `existing guards do not stop the path` because the only invalidation mechanism (`repositoryWithRefreshedGitHubRepository`) explicitly bails out early per the code comment when there's no `match`, and even when it runs it only *adds* a fresh association on top of the stale one — it doesn't first assert that content actually still corresponds to the previously stored repository/account before performing account-scoped calls with `withRefreshedGitHubRepository`: [8](#0-7) 

### Impact Explanation
This silently misapplies a privileged/trust context (which GitHub account authenticates operations, which parent repo receives "contribute to parent" pushes/PRs, cached protected-branch bypass decisions) from the old, legitimate repository onto brand-new, attacker-controlled repository content, without the user reconfirming trust — mirroring the "previous owner/stale privileged role persists after resource content/ownership changes" defect in the report. This can silently corrupt where the user's future commits, branches, and pull requests are targeted (`getNonForkGitHubRepository`) and which account's credentials Desktop uses for git operations against attacker-supplied remotes.

### Likelihood Explanation
Requires a fairly specific but realistic user flow (folder deleted/replaced or "Clone Again"/"Add existing repository" pointed at a path Desktop already tracks) with no attacker code execution or local/admin access needed — matching the "attacker controls a cloned/fetched repository" scope. Likelihood is moderate: it depends on path reuse, which is a normal, not unnatural, user action (folder deleted, re-cloned; “missing repository -> Clone Again”).

### Recommendation
Re-validate the GitHub repository association whenever a `Repository` row is reattached to disk content (on `addRepository`, `switchWorktree`, and `cloneAgain`): compare the current remote URL/owner-repo identity against the stored `GitHubRepository`, and clear (not just refresh) the association, per-`dbID` caches, and fork-contribution-target preference if they no longer match, rather than only adding fresh data on top of stale state. Resolve the long-standing TODO referencing desktop/desktop#1144 to make association clearing an explicit, first-class operation instead of "never clear."

### Proof of Concept
1. Victim adds legitimate repo `github.com/victim/real-repo` at local path `~/proj`; Desktop stores `Repository{path: ~/proj, gitHubRepositoryID: X}` tied to victim's `Account`.
2. Victim deletes `~/proj` (or it becomes "missing").
3. Victim clones an attacker-supplied repo into the exact same path `~/proj` (e.g., attacker instructs "reclone your project here" via a README/support message, or victim uses "Clone Again" pointed at a malicious `cloneURL` set on the stale `gitHubRepository` object — see `MissingRepository.cloneAgain`).
4. `RepositoriesStore.addRepository('~/proj', ...)` returns the pre-existing DB row, still carrying `gitHubRepositoryID: X` and the previous fork-contribution-target/account association.
5. Desktop performs subsequent operations (fetch PR list, apply branch-protection bypass logic, decide push/PR target via `getNonForkGitHubRepository`) using the stale association against the new, attacker-controlled repository content, without ever asking the user to reconfirm the association is still valid.

### Citations

**File:** app/src/lib/stores/repositories-store.ts (L237-252)
```typescript
  public async addRepository(
    path: string,
    gitDir: string | undefined,
    opts?: AddRepositoryOptions
  ): Promise<Repository> {
    const repository = await this.db.transaction(
      'rw',
      this.db.repositories,
      this.db.gitHubRepositories,
      this.db.owners,
      async () => {
        const existing = await this.db.repositories.get({ path })

        if (existing !== undefined) {
          return await this.toRepository(existing)
        }
```

**File:** app/src/lib/repository-matching.ts (L54-65)
```typescript
export function matchExistingRepository<T extends { readonly path: string }>(
  repos: ReadonlyArray<T>,
  path: string
): T | undefined {
  // Windows is guaranteed to be case-insensitive so we can be a bit less strict
  const normalize = __WIN32__
    ? (p: string) => Path.normalize(p).toLowerCase()
    : (p: string) => Path.normalize(p)

  const needle = normalize(path)
  return repos.find(r => normalize(r.path) === needle)
}
```

**File:** app/src/lib/stores/app-store.ts (L4880-4885)
```typescript
    // TODO: We currently never clear GitHub repository associations (see
    // https://github.com/desktop/desktop/issues/1144). So we can bail early at
    // this point.
    if (!match) {
      return repository
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

**File:** app/src/models/repository.ts (L189-210)
```typescript
export function getNonForkGitHubRepository(
  repository: RepositoryWithGitHubRepository
): GitHubRepository {
  if (!isRepositoryWithForkedGitHubRepository(repository)) {
    // If the repository is not a fork, we don't have to worry about anything.
    return repository.gitHubRepository
  }

  const forkContributionTarget = getForkContributionTarget(repository)

  switch (forkContributionTarget) {
    case ForkContributionTarget.Self:
      return repository.gitHubRepository
    case ForkContributionTarget.Parent:
      return repository.gitHubRepository.parent
    default:
      return assertNever(
        forkContributionTarget,
        'Invalid fork contribution target'
      )
  }
}
```

**File:** app/src/lib/stores/pull-request-coordinator.ts (L263-272)
```typescript
function findRepositoriesForGitHubRepository(
  gitHubRepository: GitHubRepository,
  repositories: ReadonlyArray<RepositoryWithGitHubRepository>
): ReadonlyArray<RepositoryWithGitHubRepository> {
  const { dbID } = gitHubRepository

  return repositories.filter(
    repository => getNonForkGitHubRepository(repository).dbID === dbID
  )
}
```

**File:** app/src/ui/missing-repository.tsx (L169-184)
```typescript
  private cloneAgain = async () => {
    const gitHubRepository = this.props.repository.gitHubRepository
    if (!gitHubRepository) {
      return
    }

    const cloneURL = gitHubRepository.cloneURL
    if (!cloneURL) {
      return
    }

    try {
      await this.props.dispatcher.cloneAgain(
        cloneURL,
        this.props.repository.path
      )
```
