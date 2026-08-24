### Title
Unscoped, cross-repository `cachedRepoRulesets` cache lets a ruleset ID from one endpoint mask/override enforcement state for a repository on another endpoint - (File: `app/src/lib/stores/app-store.ts`)

### Summary
The reported Sherlock bug is about state (`ActiveProposals`) that is not promptly invalidated/scoped, causing a security-relevant check (token lock) to use stale data. The closest analog in GitHub Desktop is `AppStore.cachedRepoRulesets`, a single `Map<number, IAPIRepoRuleset>` keyed only by the numeric GitHub ruleset ID, with no scoping by endpoint/owner/repo, that is consulted to decide whether repo-rule enforcement (required signatures, required PRs, branch-name/creation restrictions, bypassability) should be shown/enforced for the *currently active* repository.

### Finding Description
`refreshBranchProtectionState` populates the cache like this: [1](#0-0) 

```
const slimRulesets = await api.fetchAllRepoRulesets(owner, name)
...
for (const id of rulesetIds) {
  // check the cache and don't re-query any that are already in there
  if (!this.cachedRepoRulesets.has(id)) {
    calls.push(api.fetchRepoRuleset(owner, name, id))
  }
}
```

Note that `id` is only the numeric ruleset ID returned by `fetchAllRepoRulesets`/`fetchRepoRuleset` — it is never combined with `owner`, `name`, or `endpoint`: [2](#0-1) 

Once an ID is present in `cachedRepoRulesets`, it is never re-fetched for *any other* repository that happens to report the same numeric ruleset ID, and `_updateCachedRepoRulesets` simply merges by ID with no owner/endpoint check: [3](#0-2) 

This cache is then used to determine whether a rule is enforced or bypassable in `parseRepoRules`: [4](#0-3) 

and again for branch-name validation in `checkBranchNameRules`: [5](#0-4) 

Because GitHub Desktop supports multiple simultaneous accounts/endpoints (github.com plus one or more GitHub Enterprise Server instances), and because GHES ruleset IDs are small sequential integers assigned independently by each server, an attacker who controls a repository on an endpoint the user has added (e.g. a GHES instance, or any repo where the attacker can shape ruleset responses) can supply a ruleset whose numeric `id` collides with the `id` of a ruleset that already exists (or will exist) on a different, legitimate repository the same user works with. Since `_upsertGitHubRepository`'s permission/parent handling shows the app already anticipates "collisions/confused identity across repos with the same numeric key" as a real risk class, but no analogous protection exists for `cachedRepoRulesets`.

### Impact Explanation
If the attacker's crafted ruleset (with a colliding `id`) is fetched and cached first with `current_user_can_bypass: 'always'`, then when the user later works on the legitimate protected repository that reuses the same numeric ruleset `id`, `refreshBranchProtectionState` sees `this.cachedRepoRulesets.has(id) === true` and skips fetching the real ruleset, reusing the attacker's poisoned entry. Consequences:
- `parseRepoRules` will mark rules such as `signedCommitsRequired`, `pullRequestRequired`, `basicCommitWarning`, and `creationRestricted` as `'bypass'` instead of `true` for the legitimate repository, based on `enforced = ruleset.current_user_can_bypass === 'always' ? 'bypass' : true`.
- `CommitMessage.hasRepoRuleFailure()` therefore returns `false`, silently suppressing the warning/blocking UI that normally tells the user their commit will be rejected or is otherwise non-compliant: [6](#0-5) 
- `checkBranchNameRules` similarly treats the branch name/creation rule as bypassable and lets the user proceed "with caution" instead of blocking.

This is a silent-corruption-of-workflow-state issue: the user is misled by Desktop's UI about the compliance status of what they are about to commit/push to a legitimate, unrelated repository, purely because of state leaked from an attacker-controlled repository/endpoint object the user merely opened.

### Likelihood Explanation
Requires the victim to have added an attacker-controlled or attacker-influenced GitHub Enterprise Server endpoint/repo (a realistic, unprivileged scenario — no local access, no credentials, no malware needed) and to also work with another protected repository whose ruleset ID happens to collide with the crafted one. Because ruleset IDs on distinct GitHub Enterprise Server instances are typically small sequential integers, and the attacker fully controls what ID(s) they expose from their own repository's rules/rulesets API, engineering a collision with a commonly low-numbered legitimate ruleset ID is feasible, especially since the attacker can create many low-numbered rulesets on their own controlled instance to probe/target likely IDs. Existing guards (`protectionEnabledForBranchCache`, `branchProtectionSettingsFoundCache` in `repositories-store.ts`) are correctly scoped per `dbID`, but `cachedRepoRulesets` in `app-store.ts` is not, so none of those guards mitigate this path.

### Recommendation
Scope `cachedRepoRulesets` (and the check in `refreshBranchProtectionState`) by `(endpoint, owner, name, rulesetId)` instead of `rulesetId` alone, e.g. use a composite key similar to `getKey(dbID, name)` used in `repositories-store.ts`, so that a ruleset fetched for one repository/endpoint can never be reused to answer bypass/enforcement questions for a different repository/endpoint.

### Proof of Concept
1. Attacker controls (or the victim adds) a GitHub Enterprise Server endpoint/account in Desktop, and hosts a repository there.
2. Victim opens the attacker's repository in Desktop; `refreshBranchProtectionState` calls `fetchAllRepoRulesets`/`fetchRepoRuleset`, which return a crafted ruleset with `id: 7, current_user_can_bypass: 'always'`. This gets cached in `this.cachedRepoRulesets` via `_updateCachedRepoRulesets`.
3. Victim switches to a legitimate, unrelated github.com repository that has a real branch-protection ruleset whose `id` is also `7` (plausible collision on a low sequential ID) enforcing e.g. required signed commits (`current_user_can_bypass: 'never'` in reality).
4. `refreshBranchProtectionState`/`checkBranchNameRules` for the legitimate repo see `cachedRepoRulesets.has(7) === true` and never re-fetch the real ruleset; `parseRepoRules` uses the poisoned `bypass: 'always'` entry, so `signedCommitsRequired`/`creationRestricted` report `'bypass'`.
5. `CommitMessage.hasRepoRuleFailure()` returns `false` and the branch-name/creation warnings are suppressed, so the victim is not warned in Desktop even though the real repo rule is enforced server-side — the local commit/push workflow silently misrepresents compliance status for the legitimate repository.

### Citations

**File:** app/src/lib/stores/app-store.ts (L1526-1544)
```typescript
        const slimRulesets = await api.fetchAllRepoRulesets(owner, name)

        // ultimate goal here is to fetch all rulesets that apply to the repo
        // so they're already cached when needed later on
        if (slimRulesets?.length) {
          const rulesetIds = slimRulesets.map(r => r.id)

          const calls: Promise<IAPIRepoRuleset | null>[] = []
          for (const id of rulesetIds) {
            // check the cache and don't re-query any that are already in there
            if (!this.cachedRepoRulesets.has(id)) {
              calls.push(api.fetchRepoRuleset(owner, name, id))
            }
          }

          if (calls.length > 0) {
            const rulesets = await Promise.all(calls)
            this._updateCachedRepoRulesets(rulesets)
          }
```

**File:** app/src/lib/stores/app-store.ts (L1570-1577)
```typescript
  /** This shouldn't be called directly. See `Dispatcher`. */
  public _updateCachedRepoRulesets(rulesets: Array<IAPIRepoRuleset | null>) {
    for (const rs of rulesets) {
      if (rs !== null) {
        this.cachedRepoRulesets.set(rs.id, rs)
      }
    }
  }
```

**File:** app/src/lib/api.ts (L1722-1747)
```typescript
  /**
   * Fetches slim versions of all repo rulesets for the given repository. Utilize the cache
   * in IAppState instead of querying this if possible.
   */
  public async fetchAllRepoRulesets(
    owner: string,
    name: string
  ): Promise<ReadonlyArray<IAPISlimRepoRuleset> | null> {
    const path = `repos/${owner}/${name}/rulesets`
    try {
      const response = await this.ghRequest('GET', path)
      return await parsedResponse<ReadonlyArray<IAPISlimRepoRuleset>>(response)
    } catch (err) {
      // If the repository isn't owned by the current user there's no way for us
      // to preemptively check whether rulesets are enabled so we give it a shot
      // but there's no need to log if it fails. Same with 404s, i.e the user
      // doesn't have access to the repo any more or it's been deleted.
      if (!isRulesetsNotEnabledError(err) && !isNotFoundApiError(err)) {
        log.info(
          `[fetchAllRepoRulesets] unable to fetch all repo rulesets | ${path}`,
          err
        )
      }
      return null
    }
  }
```

**File:** app/src/lib/helpers/repo-rules.ts (L66-88)
```typescript
export async function parseRepoRules(
  rules: ReadonlyArray<IAPIRepoRule>,
  rulesets: ReadonlyMap<number, IAPIRepoRuleset>,
  repository: Repository
): Promise<RepoRulesInfo> {
  const info = new RepoRulesInfo()
  let gpgSignEnabled: boolean | undefined = undefined

  for (const rule of rules) {
    // if a ruleset is null/undefined, then act as if the rule doesn't exist because
    // we don't know what will happen when they push
    const ruleset = rulesets.get(rule.ruleset_id)
    if (ruleset == null) {
      continue
    }

    // a rule may be configured multiple times, and the strictest value always applies.
    // since the rule will not exist in the API response if it's not enforced, we know
    // we're always assigning either 'bypass' or true below. therefore, we only need
    // to check if the existing value is true, otherwise it can always be overridden.
    const enforced =
      ruleset.current_user_can_bypass === 'always' ? 'bypass' : true

```

**File:** app/src/ui/lib/branch-name-rule-validation.tsx (L87-105)
```typescript
  // check cached rulesets to see which ones the user can bypass
  let cannotBypass = false
  for (const id of toCheck) {
    const rs = cachedRepoRulesets.get(id)

    if (rs?.current_user_can_bypass !== 'always') {
      cannotBypass = true
      break
    }
  }

  if (cannotBypass) {
    return {
      error: new Error(
        `Branch name '${branchName}' is restricted by repo rules.`
      ),
      isWarning: false,
    }
  }
```

**File:** app/src/ui/changes/commit-message.tsx (L674-691)
```typescript
  private hasRepoRuleFailure(): boolean {
    const { aheadBehind, repoRulesInfo } = this.props

    if (!this.state.repoRulesEnabled) {
      return false
    }

    return (
      repoRulesInfo.basicCommitWarning === true ||
      repoRulesInfo.signedCommitsRequired === true ||
      repoRulesInfo.pullRequestRequired === true ||
      this.state.repoRuleCommitMessageFailures.status === 'fail' ||
      this.state.repoRuleCommitAuthorFailures.status === 'fail' ||
      (aheadBehind === null &&
        (repoRulesInfo.creationRestricted === true ||
          this.state.repoRuleBranchNameFailures.status === 'fail'))
    )
  }
```
