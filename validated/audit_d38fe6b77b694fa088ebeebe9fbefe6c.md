## Analysis Result

### Title
Unbounded Server-Controlled Poll Interval Allows Indefinite Suppression of Background Fetch - (File: `app/src/lib/stores/helpers/background-fetcher.ts`)

### Summary
The Solidity bug lets a privileged actor (`onlyOwner`) push `endTime` arbitrarily into the future with no upper cap, permanently blocking a time-gated safety action (refunds). The structural equivalent in GitHub Desktop is `BackgroundFetcher.getFetchInterval()`, which takes a poll interval **returned by the remote GitHub/GHE server** and uses it to schedule the next background fetch, enforcing only a **lower bound** (`MinimumInterval`) and no upper bound at all.

### Finding Description
`BackgroundFetcher.performAndScheduleFetch()` reschedules itself using a value obtained from `getFetchInterval()`: [1](#0-0) 

Inside `getFetchInterval`, the interval is derived from `api.getFetchPollInterval(...)`, a value the server (or, for self-hosted GitHub Enterprise setups / any endpoint reachable via a network path an attacker controls, a MITM/malicious proxy responding on that endpoint) fully controls:

```ts
const pollInterval = await api.getFetchPollInterval(
  repository.owner.login,
  repository.name
)
if (pollInterval) {
  interval = Math.max(pollInterval, MinimumInterval)
} else {
  interval = DefaultFetchInterval
}
``` [2](#0-1) 

The constants declared at the top of the file show the guard is one-sided — only a floor is defined, never a ceiling:

```ts
const DefaultFetchInterval = 1000 * 60 * 60
const MinimumInterval = 1000 * 5 * 60
``` [3](#0-2) 

Because `Math.max(pollInterval, MinimumInterval)` only prevents the interval from being *too small*, a server response with an extremely large poll interval (e.g. `Number.MAX_SAFE_INTEGER` or any multi-year value) is accepted verbatim and used directly as the `setTimeout` delay for scheduling the next fetch:

```ts
this.timeoutHandle = window.setTimeout(
  () => this.performAndScheduleFetch(repository),
  interval
)
``` [4](#0-3) 

This is the same broken invariant as the `extendTime()` report: a value that is supposed to gate the *timing* of a user-protective action is sourced from an untrusted/privileged-but-attacker-influenced party with no maximum bound, so that party can push the effective wait time arbitrarily far into the future and suppress the action indefinitely.

### Impact Explanation
Background fetch is Desktop's mechanism for silently keeping the local repository state (remote refs, ahead/behind counts, PR/CI status prerequisites) up to date without explicit user action. If a hostile or compromised remote/GHE endpoint (or a network-level proxy/MITM sitting on that HTTP(S) connection) returns a pathologically large poll interval, the corrupted value — `interval` in `getFetchInterval()` — is stored as the sole scheduling delay for that repository's future fetches, effectively disabling automatic fetch for the life of the app session (or until the app restarts and reschedules with initial skew). A user relying on Desktop's ambient freshness could be silently kept on stale local refs, masking upstream changes (e.g., new commits, force-pushes, or branch protection changes) that they would otherwise be alerted to via the normal fetch cadence. This is a silent-corruption-of-state class impact — not code execution — but it degrades a security-relevant invariant (freshness of what the user believes is the current remote state) using a remotely-controlled, attacker-influenced value.

### Likelihood Explanation
The likelihood is limited by scope: this path is only reachable when Desktop is pointed at an endpoint an attacker controls the responses for (a malicious/compromised GitHub Enterprise instance, or a MITM on the connection to it) — GitHub.com itself is trusted infrastructure and not attacker-controlled in the threat model implied by "Valid Impact" (git remote/proxy response). Given that scope, exploitation requires no user interaction beyond having added/cloned a repository pointing at that endpoint, and no special privileges — it is a straightforward, low-effort manipulation of a single numeric field in an HTTP response.

### Recommendation
Impose a symmetric cap, e.g. `Math.min(Math.max(pollInterval, MinimumInterval), MaximumInterval)`, so the server-supplied interval cannot exceed some sane ceiling (for example, a small multiple of `DefaultFetchInterval`). This mirrors the audit's recommendation of capping `extendTime()` in `DaosLive.sol` to prevent unbounded manipulation of a time-gated safety mechanism.

### Proof of Concept
1. Point Desktop at a GitHub Enterprise endpoint under attacker control (or intercept the connection to one via a compromised proxy/MITM).
2. Have that endpoint answer the request underlying `api.getFetchPollInterval(owner, name)` with an extremely large interval value (e.g. equivalent to `Number.MAX_SAFE_INTEGER` milliseconds).
3. `getFetchInterval()` computes `interval = Math.max(hugeValue, MinimumInterval)` → `hugeValue`, with no upper clamp. [5](#0-4) 
4. `performAndScheduleFetch` schedules the next fetch with `window.setTimeout(..., interval)` using that huge delay. [6](#0-5) 
5. Background fetching for that repository is effectively suspended indefinitely, and the user's local view of the remote silently stops refreshing.

**Note on verification limits:** I was unable to locate and inspect the exact body of `API.getFetchPollInterval` in `app/src/lib/api.ts` (only its call sites were found via search, not the implementation, likely due to index size limits). I could not confirm the exact HTTP header/field it parses or whether any additional sanitization exists there beyond what's shown in `background-fetcher.ts`. If you need the precise implementation details, a Devin session with full file access would be able to pull the complete `api.ts` source to confirm.

### Citations

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L11-17)
```typescript
const DefaultFetchInterval = 1000 * 60 * 60

/**
 * A minimum fetch interval, to protect against the server accidentally sending
 * us a crazy value.
 */
const MinimumInterval = 1000 * 5 * 60
```

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L107-115)
```typescript
    const interval = await this.getFetchInterval(repository)
    if (this.stopped) {
      return
    }

    this.timeoutHandle = window.setTimeout(
      () => this.performAndScheduleFetch(repository),
      interval
    )
```

**File:** app/src/lib/stores/helpers/background-fetcher.ts (L118-148)
```typescript
  /** Get the allowed fetch interval from the server. */
  private async getFetchInterval(
    repository: GitHubRepository
  ): Promise<number> {
    const account = getAccountForEndpoint(
      await this.accountsStore.getAll(),
      repository.endpoint
    )

    let interval = DefaultFetchInterval

    if (account) {
      const api = API.fromAccount(account)

      try {
        const pollInterval = await api.getFetchPollInterval(
          repository.owner.login,
          repository.name
        )
        if (pollInterval) {
          interval = Math.max(pollInterval, MinimumInterval)
        } else {
          interval = DefaultFetchInterval
        }
      } catch (e) {
        log.error('Error fetching poll interval', e)
      }
    }

    return interval + skewInterval()
  }
```
