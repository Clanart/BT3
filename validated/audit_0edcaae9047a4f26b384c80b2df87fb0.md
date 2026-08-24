### Title
Global, endpoint/repo-unscoped `cachedRepoRulesets` cache lets an attacker-controlled ruleset silently downgrade branch-protection/repo-rule enforcement in another repository - ([File: app/src/lib/stores/app-store.ts])

### Summary
The Concur `Shelter` bug is a state-reset vulnerability: an attacker-controlled actor resets a cached timestamp gate (`activated[_token]`) without re-validating the underlying condition, and downstream code trusts the stale/attacker-influenced cache to make a security decision (allow withdrawal vs. deactivate/drain). The Desktop analog is `IAppState.cachedRepoRulesets`, a single map keyed only by a bare numeric ruleset `id`, populated from GitHub API responses and consumed application-wide (across all repositories/accounts) to decide whether a repo rule is enforced or bypassable, without ever re-verifying that the cached ruleset actually belongs to the repository currently being evaluated.

### Finding Description
`cachedRepoRulesets` is declared as a single, app-global cache: [1](#0-0) 

It is populated in `refreshBranchProtectionState`, keyed purely by the numeric `id` returned from the API, with no association to the owner/repo/endpoint that produced it, and explicitly skips re-fetching any ruleset ID already present: [2](#0-1) 

The critical field cached per ruleset is `current_user_can_bypass`, which directly controls whether a rule violation is treated as a hard block or a soft, bypassable warning: [3](#0-2) 

`parseRepoRules` uses exactly that cached value (`ruleset.current_user_can_bypass === 'always' ? 'bypass' : true`) to decide the enforcement strength of every rule type (commit signing, commit message/author/committer patterns, branch name patterns, PR requirements, etc.), and if the ruleset for a rule's `ruleset_id` is missing from the cache it is treated as non-existent rather than unknown: [4](#0-3) 

This same shared cache is consumed directly by the branch-name validation flow used when creating/renaming branches, again with no re-validation against the specific repository/endpoint: [5](#0-4) 

Because the cache key is just `id: number` (an `IAPISlimRepoRuleset`/`IAPIRepoRuleset` field returned verbatim by whichever endpoint answered the request), any code path that can get Desktop to fetch or accept a ruleset object with a chosen `id` and `current_user_can_bypass: 'always'` "poisons" that ID for every other repository Desktop subsequently checks that references the same numeric ID — exactly like the Shelter bug where a `activate` call that the client fully controls resets shared state (`activated[_token]`) that a later, unrelated check (`withdraw`/`deactivate`) blindly trusts. In both cases: (1) the cache/gate is a coarse, shared piece of state, (2) an attacker-influenced write to it is accepted without validating it corresponds to the entity currently being checked, and (3) a subsequent security decision (withdraw permission / rule-bypass permission) is made purely from that stale/attacker-set value.

### Impact Explanation
If an attacker can cause a favorable (`current_user_can_bypass: 'always'`) ruleset entry with a given `id` to be cached — e.g., via a malicious/compromised GitHub Enterprise instance, a MITM'd API response for a decoy repository the user also has open in Desktop, or a repository whose rulesets the attacker controls and can shape the numeric `id` of (GitHub ruleset IDs are opaque, globally-scoped integers, not verified by Desktop to belong to the current owner/repo) — Desktop will silently downgrade enforcement of that ruleset ID everywhere else it's referenced in the session: branch-protection warnings become soft "Proceed with caution!" bypass prompts instead of hard blocks, and users can be induced to push commits/branches or complete multi-commit operations that violate the real repository's rules, silently corrupting what the user commits/pushes without the intended server-side-mirrored guardrail.

### Likelihood Explanation
Exploitation requires that the victim have multiple GitHub-connected repositories open in the same Desktop session and that an attacker can influence at least one repo-rules/ruleset API response (compromised/malicious GHE endpoint, network-level MITM of an API response, or control of a ruleset ID an attacker can align with a target) reachable from the "unprivileged, attacker controls a git remote/GitHub API object/proxy response" threat model in scope. The exact predictability/collision of numeric ruleset IDs across independent repositories could not be fully confirmed from the available source (no server-side ID-allocation code is present in this client repo), so likelihood is assessed as plausible but not fully verified — this would need confirmation of GitHub's ruleset ID allocation scheme.

### Recommendation
Scope the `cachedRepoRulesets` cache key by `(endpoint, owner, name, id)` instead of bare numeric `id`, or otherwise re-validate that a cached ruleset entry's owning repository matches the repository currently being evaluated before trusting its `current_user_can_bypass` value, and invalidate/refresh per-repository rather than relying on a single cross-repository cache hit test (`!this.cachedRepoRulesets.has(id)`).

### Proof of Concept
Not independently executable from static analysis alone; conceptually:
1. Open Repository A (github.com, real org) and Repository B (attacker-controlled or attacker-influenced GHE/malicious-proxy endpoint) simultaneously in Desktop.
2. Cause Desktop to fetch rulesets for Repository B such that the API response includes a ruleset object `{ id: N, current_user_can_bypass: 'always' }` for a chosen `N` — see `fetchAllRepoRulesets`/`fetchRepoRuleset` in [6](#0-5) .
3. This populates `cachedRepoRulesets.set(N, {...bypass: 'always'})` via `_updateCachedRepoRulesets` ( [7](#0-6) ).
4. Later, when Repository A's branch/commit is checked against a real ruleset that also has `id === N`, `refreshBranchProtectionState` skips re-fetching it (`if (!this.cachedRepoRulesets.has(id))`), and `parseRepoRules`/`checkBranchNameRules` use the poisoned `'always'` bypass value, converting what should be a hard block into a bypassable "Proceed with caution!" warning for Repository A.

Full verification of ruleset-ID collision feasibility across independently-owned repositories was not possible from the client-side source alone; a Devin session with API/network access would be needed to confirm GitHub's ruleset ID allocation semantics and fully validate exploitability end-to-end.

### Citations

**File:** app/src/lib/app-state.ts (L397-402)
```typescript

  /**
   * Cached repo rulesets. Used to prevent repeatedly querying the same
   * rulesets to check their bypass status.
   */
  readonly cachedRepoRulesets: ReadonlyMap<number, IAPIRepoRuleset>
```

**File:** app/src/lib/stores/app-store.ts (L1524-1544)
```typescript
      let currentRepoRulesInfo = new RepoRulesInfo()
      if (useRepoRulesLogic(account, repository)) {
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

**File:** app/src/lib/stores/app-store.ts (L1571-1577)
```typescript
  public _updateCachedRepoRulesets(rulesets: Array<IAPIRepoRuleset | null>) {
    for (const rs of rulesets) {
      if (rs !== null) {
        this.cachedRepoRulesets.set(rs.id, rs)
      }
    }
  }
```

**File:** app/src/lib/api.ts (L577-585)
```typescript
/**
 * A ruleset returned from the GitHub API's "get a ruleset for a repo" endpoint.
 */
export interface IAPIRepoRuleset extends IAPISlimRepoRuleset {
  /**
   * Whether the user making the API request can bypass the ruleset.
   */
  readonly current_user_can_bypass: 'always' | 'pull_requests_only' | 'never'
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

**File:** app/src/lib/helpers/repo-rules.ts (L66-87)
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
