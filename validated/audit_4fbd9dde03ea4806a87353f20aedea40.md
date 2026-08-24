Found a strong analog: the `fetchPushControl` fallback in the GitHub API client.

### Title
Branch-protection API failure silently falls back to "fully permissive" push control, allowing force-push/deletion of protected branches when GitHub is unreachable/errors - ([File: app/src/lib/api.ts])

### Summary
The external report's broken invariant is: *when a security-relevant oracle call fails (times out/errors), the code substitutes a maximally permissive default (price = 0/FIX_MAX) instead of treating the failure as "unknown," and that default is then used to drive a destructive, irreversible action (selling all RSR at zero).* The Desktop analog is `GitHubAPI.fetchPushControl` in [1](#0-0) , which on any network/API error returns a hard-coded object granting full push/force-push/delete permissions instead of propagating "unknown" state.

### Finding Description
`fetchPushControl` is documented explicitly: *"Note: if request fails, the default returned value assumes full access for the user"* [2](#0-1) . On failure it returns:
```
{
  pattern: null,
  required_signatures: false,
  required_status_checks: [],
  required_approving_review_count: 0,
  required_linear_history: false,
  allow_actor: true,
  allow_deletions: true,
  allow_force_pushes: true,
}
``` [3](#0-2) 

This mirrors the `rsrAsset.price()` fallback of `(0, FIX_MAX)` on oracle timeout in the report: an error/unavailable condition is coerced into the *most permissive possible* value rather than a safe/blocking one, and downstream logic (Desktop's force-push confirmation gating, branch-protection UI warnings) consumes this value as if it were a real, authoritative answer from GitHub. A repository/branch-protection state is "attacker or environment controlled" in the sense that: a compromised/rogue GHES instance, a captive-portal/proxy intercepting the API call, a transient GitHub outage, or a network adversary who can simply drop/delay/error the `push_control` request can force this code path. Once triggered, Desktop's UI logic that normally warns/blocks a force-push or branch deletion when a branch is protected (`required_approving_review_count`, `allow_force_pushes`, etc.) instead treats the branch as unprotected, silently allowing the user to force-push over or delete a rule-protected branch without the confirmation/blocking dialog that would otherwise appear.

### Impact Explanation
The corrupted value is the entire `IAPIPushControl` object consumed by branch-protection UI/dispatcher logic that decides whether to warn on/allow a force-push or deletion. Silently treating "unknown protection state" as "protection fully disabled" can cause a user to inadvertently force-push over or delete a protected branch (destructive rewrite/loss of commits on the remote) with no warning — the exact "silent corruption of what the user commits or pushes" class called out as valid impact. Unlike the Reserve bug (funds transferred), the analog's blast radius is repository/branch integrity rather than funds, but it is a genuine, unprompted, non-social-engineering path: it triggers purely from an API/network failure that an attacker (rogue proxy/MITM or malicious GHES) can induce.

### Likelihood Explanation
Any transient failure of the branch-protection endpoint — timeouts, 5xx from GitHub/GHES, a captured/blocked request by a network intermediary, or a compromised enterprise proxy — is sufficient; no user action beyond a normal push/force-push attempt is required. This is a comment-documented, intentional fallback (not a bug introduced accidentally), which increases confidence that it's real reachable behavior rather than dead code, but it does require the request to fail at exactly the moment the protection check runs, which is opportunistic rather than fully attacker-timed.

### Recommendation
Change the failure fallback to represent "unknown," not "fully open": e.g. return `null` (as `fetchProtectedBranches` correctly does on failure, see `app/src/lib/api.ts:1676-1691`) and have callers treat `null`/unknown push-control as the *most restrictive* state (block/warn on force-push and deletion) rather than the least restrictive one, consistent with a fail-closed design instead of fail-open.

### Proof of Concept
1. Configure a repository with branch protection (`required_approving_review_count > 0`, `allow_force_pushes: false`) on the current branch.
2. Intercept/black-hole the `GET /repos/{owner}/{name}/branches/{branch}/push_control` request (e.g., via a MITM proxy, a firewall rule, or simulate a GHES outage) while leaving other API calls (auth, push) functional.
3. Observe that `fetchPushControl` throws internally, is caught, and returns the hard-coded object at [3](#0-2)  with `allow_force_pushes: true` and `required_approving_review_count: 0`.
4. Any Desktop UI logic gating a force-push/deletion warning on this value will treat the branch as unprotected and allow the destructive push/deletion to proceed without the expected confirmation, in contrast to the safe `null`-on-failure pattern used by the neighboring `fetchProtectedBranches` ( [4](#0-3) ).

### Citations

**File:** app/src/lib/api.ts (L1629-1668)
```typescript
  /**
   * Get branch protection info to determine if a user can push to a given branch.
   *
   * Note: if request fails, the default returned value assumes full access for the user
   */
  public async fetchPushControl(
    owner: string,
    name: string,
    branch: string
  ): Promise<IAPIPushControl> {
    const path = `repos/${owner}/${name}/branches/${encodeURIComponent(
      branch
    )}/push_control`

    const headers: any = {
      Accept: 'application/vnd.github.phandalin-preview',
    }

    try {
      const response = await this.ghRequest('GET', path, {
        customHeaders: headers,
      })
      return await parsedResponse<IAPIPushControl>(response)
    } catch (err) {
      log.info(
        `[fetchPushControl] unable to check if branch is potentially pushable`,
        err
      )
      return {
        pattern: null,
        required_signatures: false,
        required_status_checks: [],
        required_approving_review_count: 0,
        required_linear_history: false,
        allow_actor: true,
        allow_deletions: true,
        allow_force_pushes: true,
      }
    }
  }
```

**File:** app/src/lib/api.ts (L1676-1691)
```typescript
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
