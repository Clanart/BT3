## Finding

### Title
Unbounded, non-timed-out `fetch()` calls in the GitHub API client allow a malicious/compromised GitHub Enterprise Server (or intercepted API response) to exhaust renderer memory - (File: `app/src/lib/http.ts`, `app/src/lib/api.ts`)

### Summary
GitHub Desktop's core HTTP client, `request()` in [1](#0-0) , is used by virtually every method of the `API` class in `app/src/lib/api.ts` (issues, PRs, releases, mentionables, rulesets, pagination via `fetchAll`, etc.) to talk to whatever endpoint the user's account is configured against — `https://api.github.com` for GitHub.com or a self-hosted GitHub Enterprise Server URL entered by the user. This call has none of the anti-DoS safeguards the original report calls out as missing from Axios/Node-Fetch: no request timeout, no response-size cap, and no redirect restriction. `deserialize()` then unconditionally calls `response.json()`, which buffers the *entire* response body into renderer memory before any validation occurs, exactly the "malicious HTTP response" primitive from the report.

### Finding Description
Every outbound authenticated GitHub API call goes through: [2](#0-1) 

There is no `AbortController`/timeout, no `redirect: 'error'`, and no cap on response size. The body is always fully materialized in `deserialize()`: [3](#0-2) 

Interestingly, the codebase already demonstrates awareness of this exact class of risk elsewhere in the same file/module: `isGitHubHost()` explicitly sets a 2-second `AbortController` timeout and `redirect: 'error'` when probing a host: [4](#0-3) 

That hardening was never propagated to `request()`/`ghRequest()`, which is the function actually used for the bulk of API traffic — `fetchAll` (paginated issue/PR/comment listing), `fetchMentionables`, `fetchProtectedBranches`, `fetchRepoRuleset`, etc. — all resolving through: [5](#0-4) [6](#0-5) 

The endpoint these calls hit is not always `api.github.com`: Desktop supports adding GitHub Enterprise Server accounts at arbitrary user-supplied hostnames, and the endpoint for a repository is derived straight from its git remote host: [7](#0-6) 

Any server willing to answer as that endpoint (a rogue/compromised GHES instance, or a network position able to answer for it) can trigger every one of the dozens of `ghRequest()`-based calls Desktop makes during normal use (viewing PRs, fetching mentionables, checking rulesets, etc.) and respond with either (a) a very large JSON body, exhausting renderer heap while `response.json()` parses it, or (b) a slow, never-completing body, tying up the fetch indefinitely since there is no timeout to abort it. The existing `installSameOriginFilter` (`app/src/main-process/same-origin-filter.ts`) only strips `Authorization`/`Cookie` headers on cross-origin redirects — it does nothing to bound response size or enforce a timeout, so it does not stop this path.

### Impact Explanation
A malicious or compromised GitHub Enterprise Server endpoint can force the Desktop renderer to buffer arbitrarily large or indefinitely-streaming HTTP response bodies with no timeout and no size ceiling, leading to renderer memory exhaustion, UI freezes, and application crashes — the same "application-level DoS via HTTP response manipulation" pattern the source report demonstrates against Shardeum's Axios/Node-Fetch usage. This is Medium/High relative to the report's own scale for the equivalent "Explorer/Relayer/Archiver-connects-to-less-trusted-endpoint" categories.

### Likelihood Explanation
Exploitation requires the victim to have an account against an endpoint the attacker controls or can intercept (typically a GitHub Enterprise Server) — a supported, first-class Desktop workflow, not a local-access/admin/malware/social-engineering precondition. Once such an account exists, every routine action that triggers `ghRequest()`/`fetchAll()` (opening PRs, loading mentionable users, refreshing rulesets, etc.) is a trigger point, matching the report's "By passively listening for and answering to any external calls initiated by a vulnerable component" exploitation method.

### Recommendation
Apply the same mitigations `isGitHubHost()` already uses to the shared `request()` path in `app/src/lib/http.ts`:
- Add a round-trip timeout via `AbortController` on all requests, not just the host-discovery check.
- Set an explicit response size ceiling (reject/abort once `Content-Length` or streamed byte count exceeds a sane bound) before calling `response.json()`.
- Consider constraining `redirect` behavior for authenticated API calls the way `isGitHubHost()` restricts it for discovery.

### Proof of Concept
1. Configure a GitHub Enterprise Server account in Desktop pointing to an attacker-controlled or compromised host (e.g. `https://ghes.attacker.example/api/v3`).
2. Have that server respond to a routine endpoint invoked by Desktop (e.g. `repos/{owner}/{name}/mentionables/users`, hit via `fetchMentionables` in [8](#0-7) ) with either a multi-gigabyte JSON array or a `200 OK` response that sends headers and then never completes the body.
3. Trigger the corresponding Desktop action (e.g. open the repository so `GitHubUserStore.updateMentionables` runs, per [9](#0-8) ).
4. Observe the renderer process's memory climb without bound (large-body case) or the request hang indefinitely (slow-body case), since `request()`/`deserialize()` impose no timeout or size limit, eventually causing high memory pressure or a hung/crashed renderer.

### Citations

**File:** app/src/lib/http.ts (L63-76)
```typescript
async function deserialize<T>(response: Response): Promise<T> {
  try {
    const json = await response.json()
    return json as T
  } catch (e) {
    const contentLength = response.headers.get('Content-Length') || '(missing)'
    const requestId = response.headers.get('X-GitHub-Request-Id') || '(missing)'
    log.warn(
      `deserialize: invalid JSON found at '${response.url}' - status: ${response.status}, length: '${contentLength}' id: '${requestId}'`,
      e
    )
    throw e
  }
}
```

**File:** app/src/lib/http.ts (L116-153)
```typescript
export function request(
  endpoint: string,
  token: string | null,
  method: HTTPMethod,
  path: string,
  jsonBody?: Object,
  customHeaders?: Object,
  reloadCache: boolean = false
): Promise<Response> {
  const url = getAbsoluteUrl(endpoint, path)

  let headers: any = {
    Accept: 'application/vnd.github.v3+json, application/json',
    'Content-Type': 'application/json',
    'User-Agent': getUserAgent(),
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  headers = {
    ...headers,
    ...customHeaders,
  }

  const options: RequestInit = {
    headers,
    method,
    body: JSON.stringify(jsonBody),
  }

  if (reloadCache) {
    options.cache = 'reload' as RequestCache
  }

  return fetch(url, options)
}
```

**File:** app/src/lib/api.ts (L1806-1826)
```typescript
  /** Make an authenticated request to the client's endpoint with its token. */
  private async request(
    endpoint: string,
    method: HTTPMethod,
    path: string,
    options: {
      body?: Object
      customHeaders?: Object
      reloadCache?: boolean
    } = {}
  ): Promise<Response> {
    return await request(
      endpoint,
      this.token,
      method,
      path,
      options.body,
      options.customHeaders,
      options.reloadCache
    )
  }
```

**File:** app/src/lib/api.ts (L1832-1859)
```typescript
  private async ghRequest(
    method: HTTPMethod,
    path: string,
    options: {
      body?: Object
      customHeaders?: Object
      reloadCache?: boolean
    } = {}
  ): Promise<Response> {
    const response = await this.request(this.endpoint, method, path, options)

    // Only consider invalid token when the status is 401 and the response has
    // the X-GitHub-Request-Id header, meaning it comes from GH(E) and not from
    // any kind of proxy/gateway. For more info see #12943
    // We're also not considering a token has been invalidated when the reason
    // behind a 401 is the fact that any kind of 2 factor auth is required.
    if (
      response.status === HttpStatusCode.Unauthorized &&
      response.headers.has('X-GitHub-Request-Id') &&
      !response.headers.has('X-GitHub-OTP')
    ) {
      API.emitTokenInvalidated(this.endpoint, this.token)
    }

    tryUpdateEndpointVersionFromResponse(this.endpoint, response)

    return response
  }
```

**File:** app/src/lib/api.ts (L2037-2075)
```typescript
  /** Fetch the mentionable users for the repository. */
  public async fetchMentionables(
    owner: string,
    name: string,
    etag: string | undefined
  ): Promise<IAPIMentionablesResponse | null> {
    // NB: this custom `Accept` is required for the `mentionables` endpoint.
    const headers: any = {
      Accept: 'application/vnd.github.jerry-maguire-preview',
    }

    if (etag !== undefined) {
      headers['If-None-Match'] = etag
    }

    try {
      const path = `repos/${owner}/${name}/mentionables/users`
      const response = await this.ghRequest('GET', path, {
        customHeaders: headers,
      })

      if (response.status === HttpStatusCode.NotFound) {
        log.warn(`fetchMentionables: '${path}' returned a 404`)
        return null
      }

      if (response.status === HttpStatusCode.NotModified) {
        return null
      }
      const users = await parsedResponse<ReadonlyArray<IAPIMentionableUser>>(
        response
      )
      const etag = response.headers.get('etag') || undefined
      return { users, etag }
    } catch (e) {
      log.warn(`fetchMentionables: failed for ${owner}/${name}`, e)
      return null
    }
  }
```

**File:** app/src/lib/api.ts (L2267-2280)
```typescript
/**
 * Map a repository's URL to the endpoint associated with it. For example:
 *
 * https://github.com/desktop/desktop -> https://api.github.com
 * http://github.mycompany.com/my-team/my-project -> http://github.mycompany.com/api
 */
export function getEndpointForRepository(url: string): string {
  const parsed = URL.parse(url)
  if (parsed.hostname === 'github.com') {
    return getDotComAPIEndpoint()
  } else {
    return `${parsed.protocol}//${parsed.hostname}/api`
  }
}
```

**File:** app/src/lib/api.ts (L2467-2479)
```typescript
  const metaUrl = `${endpoint}/meta?ghd=${crypto.randomUUID()}`

  const ac = new AbortController()
  const timeoutId = setTimeout(() => ac.abort(), 2000)
  suppressCertificateErrorFor(metaUrl)
  try {
    const response = await fetch(metaUrl, {
      headers: { 'user-agent': getUserAgent() },
      signal: ac.signal,
      credentials: 'omit',
      method: 'HEAD',
      redirect: 'error',
    })
```

**File:** app/src/lib/stores/github-user-store.ts (L74-97)
```typescript
  /** Update the mentionable users for the repository. */
  public async updateMentionables(
    repository: GitHubRepository,
    account: Account
  ): Promise<void> {
    const api = API.fromAccount(account)

    const cacheEntry = await this.database.getMentionableCacheEntry(
      repository.dbID
    )

    if (
      cacheEntry !== undefined &&
      Date.now() - cacheEntry.lastUpdated < MaxFetchFrequency
    ) {
      return
    }

    const response = await api.fetchMentionables(
      repository.owner.login,
      repository.name,
      cacheEntry?.eTag
    )

```
