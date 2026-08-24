## Title
Repo-rules "fail open" on GitHub API error silently disables commit-blocking safeguards - (File: `app/src/lib/api.ts`)

### Summary
The BAMM.sol issue reduces to a general "fail-open on external data unavailability" bug: when an external oracle/service call fails, the code returns a benign-looking `0`/empty result instead of surfacing the failure, and that empty result is then treated by calling code as "no risk, proceed" rather than "unknown, be cautious." The closest reachable analog in GitHub Desktop is `API.fetchRepoRulesForBranch`, which returns an empty array whenever the GitHub API call throws (network error, GitHub API outage, rate limiting, etc.), and that empty array is indistinguishable downstream from "this branch genuinely has no rules."

### Finding Description
`fetchRepoRulesForBranch` fetches the repo rules that apply to a branch, and on any error other than a recognized "rulesets not enabled" or 404 case, it logs and then returns `new Array<IAPIRepoRule>()`: [1](#0-0) 

This empty-array result is functionally identical to the success case where the branch truly has zero rules — there is no sentinel (e.g. `null`) to signal "we couldn't determine the rules." Compare this to `fetchProtectedBranches`, in the same file, which correctly distinguishes failure (`null`) from empty success (`[]`): [2](#0-1) 
and this distinction is explicitly unit-tested for `fetchProtectedBranches`: [3](#0-2) 

No equivalent test exists asserting that `fetchRepoRulesForBranch` failures are distinguishable from "no rules." Downstream, `parseRepoRules` (in `app/src/lib/helpers/repo-rules.ts`) iterates over whatever rule array it's given to compute `RepoRulesInfo`, including `signedCommitsRequired`, `basicCommitWarning` (blocks pushing), and `pullRequestRequired`: [4](#0-3) 
If the rules array is empty because the API call failed rather than because no rules exist, `parseRepoRules` will compute a `RepoRulesInfo` with all warnings unset (`false`/`undefined`), and the UI (`commit-message.tsx`) will render no "branch protected"/"signed commits required" warning at all, exactly like the price-feed-down case computing a price of `0` and treating it as a legitimate "no purchase" outcome instead of an error condition.

### Impact Explanation
When GitHub's API is unreachable, rate-limited, or erroring at the moment Desktop refreshes repo rules, the user's commit-composer UI will silently show no repo-rule warnings (no "branch is protected," no "commits must be signed," no "PR required" banner) even though such rules exist on the remote. The user may then commit and attempt to push believing they are compliant. This matches the "silent corruption of what the user commits or pushes" impact category: the local view of push/commit safety is wrong due to a transient API failure being conflated with "no rules apply." However, actual rule enforcement itself is server-side (GitHub will still reject rule-violating pushes), so the practical damage is a false sense of safety / wasted work rather than an actual bypass of GitHub's enforcement — it degrades Desktop's advisory warnings rather than corrupting the pushed content itself.

### Likelihood Explanation
This triggers under nothing more than ordinary conditions attackers cannot directly control: any transient GitHub API failure, rate-limiting, or Enterprise-server hiccup while `fetchRepoRulesForBranch` is invoked (which the code comments show is a known, anticipated condition — it explicitly already special-cases "rulesets not enabled" and 404 to be silent, implying other errors were considered but only logged, not surfaced as "unknown state"). This does not require attacker-controlled repo content, phishing, or local access — it can occur passively during normal use of a repository with active repo rules.

### Recommendation
Change `fetchRepoRulesForBranch` to return `null` (or another explicit sentinel) on unexpected errors, mirroring `fetchProtectedBranches`, and have callers (e.g. wherever `RepoRulesInfo` is computed and cached in `app-store.ts`/`repository-state-cache.ts`) treat `null` as "rules unknown" — either retaining the last known-good `RepoRulesInfo`, or showing a distinct "couldn't verify repo rules" warning — instead of silently treating the failure as "no rules."

### Proof of Concept
1. Configure a repository whose default branch has an active ruleset requiring signed commits (`RequiredSignatures`) or blocking commits (`RequiredStatusChecks`/`Update`).
2. Simulate a transient GitHub API failure for the `repos/{owner}/{name}/rules/branches/{branch}` endpoint (e.g., temporary 5xx, network drop, or rate-limit response that isn't 404) while Desktop is loading branch rules.
3. Observe `fetchRepoRulesForBranch` catch the error and return `[]` per [5](#0-4) .
4. `parseRepoRules` receives an empty `rules` array and produces a `RepoRulesInfo` with no warnings set, per [6](#0-5) .
5. The commit composer (`commit-message.tsx`) shows no protected-branch/signed-commit/PR-required warning, even though the branch is in fact rule-protected, letting the user commit/push without the intended advisory guardrail.

Note: I was unable to fully trace how `RepoRulesInfo` is cached/retried in `app-store.ts` (e.g., whether a subsequent successful fetch overwrites the stale empty state, or whether retries happen at all) within the available indexed context — a Devin session with full repo access would be needed to confirm the exact caching/retry behavior and whether any UI-level fallback exists.

### Citations

**File:** app/src/lib/api.ts (L1670-1691)
```typescript
  /**
   * Fetch the repository's protected branches.
   *
   * Returns an empty array when the request succeeds and no protected branches
   * exist, or null when the protected branch list could not be refreshed.
   */
  public async fetchProtectedBranches(
    owner: string,
    name: string
  ): Promise<ReadonlyArray<IAPIBranch> | null> {
    const path = `repos/${owner}/${name}/branches?protected=true`
    try {
      const response = await this.ghRequest('GET', path)
      return await parsedResponse<IAPIBranch[]>(response)
    } catch (err) {
      log.info(
        `[fetchProtectedBranches] unable to list protected branches`,
        err
      )
      return null
    }
  }
```

**File:** app/src/lib/api.ts (L1696-1719)
```typescript
  public async fetchRepoRulesForBranch(
    owner: string,
    name: string,
    branch: string
  ): Promise<ReadonlyArray<IAPIRepoRule>> {
    const path = `repos/${owner}/${name}/rules/branches/${encodeURIComponent(
      branch
    )}`
    try {
      const response = await this.ghRequest('GET', path)
      return await parsedResponse<IAPIRepoRule[]>(response)
    } catch (err) {
      // If the repository isn't owned by the current user there's no way for us
      // to preemptively check whether rulesets are enabled so we give it a shot
      // but there's no need to log if it fails. Same with 404s, i.e the user
      // doesn't have access to the repo any more or it's been deleted.
      if (!isRulesetsNotEnabledError(err) && !isNotFoundApiError(err)) {
        log.info(
          `[fetchRepoRulesForBranch] unable to fetch repo rules for branch: ${branch} | ${path}`,
          err
        )
      }
      return new Array<IAPIRepoRule>()
    }
```

**File:** app/test/unit/api-test.ts (L211-217)
```typescript
    it('returns null when the request fails', async () => {
      const api = createAPI(async () => {
        throw new Error('Network request failed')
      })

      assert.equal(await api.fetchProtectedBranches('desktop', 'desktop'), null)
    })
```

**File:** app/src/lib/helpers/repo-rules.ts (L62-138)
```typescript
/**
 * Parses the GitHub API response for a branch's repo rules into a more useable
 * format.
 */
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

    switch (rule.type) {
      case APIRepoRuleType.Update:
      case APIRepoRuleType.RequiredDeployments:
      case APIRepoRuleType.RequiredStatusChecks:
        info.basicCommitWarning =
          info.basicCommitWarning !== true ? enforced : true
        break

      case APIRepoRuleType.Creation:
        info.creationRestricted =
          info.creationRestricted !== true ? enforced : true
        break

      case APIRepoRuleType.RequiredSignatures:
        // check if the user has commit signing configured. if they do, the rule
        // passes and doesn't need to be warned about.
        gpgSignEnabled ??=
          (await getBooleanConfigValue(repository, 'commit.gpgsign')) ?? false

        if (gpgSignEnabled !== true) {
          info.signedCommitsRequired =
            info.signedCommitsRequired !== true ? enforced : true
        }
        break

      case APIRepoRuleType.PullRequest:
        info.pullRequestRequired =
          info.pullRequestRequired !== true ? enforced : true
        break

      case APIRepoRuleType.CommitMessagePattern:
        info.commitMessagePatterns.push(toMetadataRule(rule, enforced))
        break

      case APIRepoRuleType.CommitAuthorEmailPattern:
        info.commitAuthorEmailPatterns.push(toMetadataRule(rule, enforced))
        break

      case APIRepoRuleType.CommitterEmailPattern:
        info.committerEmailPatterns.push(toMetadataRule(rule, enforced))
        break

      case APIRepoRuleType.BranchNamePattern:
        info.branchNamePatterns.push(toMetadataRule(rule, enforced))
        break
    }
  }

  return info
}
```
