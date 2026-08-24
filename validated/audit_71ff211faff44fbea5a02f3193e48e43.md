### Title
`isGitHubHost`'s loose hostname regex misclassifies attacker-controlled domains as GitHub Enterprise, causing OAuth secrets/credentials to be routed to attacker hosts during git credential resolution - (File: `app/src/lib/api.ts`)

### Summary
`isDotCom`/`isGHE` do exact hostname checks, but the fallback heuristic in `isGitHubHost` uses an unanchored regex, `/(^|\.)(github)\./`, to decide whether an arbitrary endpoint "appears to" be a GitHub host [1](#0-0) . Any attacker-owned domain that merely starts with `github.` (e.g. `github.attacker.com`) or contains `.github.` as a substring anywhere in the hostname satisfies this test, causing Desktop to trust it as if it were a real GitHub Enterprise endpoint, analogous to the hardcoded/incorrect address in the Sherlock report breaking a supposedly-fixed identity check.

### Finding Description
`isGitHubHost` is the fallback classifier used when an endpoint isn't `github.com`/`api.github.com` and isn't under `.ghe.com` [2](#0-1) . Its final heuristic:

```
// github.example.com,
if (/(^|\.)(github)\./.test(hostname)) {
  return true
}
``` [1](#0-0) 

matches any hostname where the literal `github.` appears at the start or immediately after a dot — this is not scoped to a specific registrable domain, so `github.attacker.com`, `foo.github.attacker.com`, etc., all satisfy it despite being fully attacker-owned.

This function is consumed by the git credential-helper trampoline to classify an endpoint before deciding how to source git credentials: `getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts` calls `isGitHubHost(endpoint)` and returns `'enterprise'` when it returns true [3](#0-2) . When Desktop performs a git operation (clone/fetch/push) against a remote whose host satisfies the loose regex but has no existing stored account, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)` [4](#0-3) , which drives the user into what appears to be a normal "Enterprise" sign-in for that host.

The subsequent OAuth flow computes its authorize/token-exchange base URL directly from the attacker-controlled `endpoint` via `getHTMLURL(endpoint)`, since the endpoint is neither dotcom nor recognized `.ghe.com` [5](#0-4) . `requestOAuthToken` then POSTs `client_id`/`client_secret` (Desktop's bundled OAuth app secret) to that same attacker-controlled `urlBase` during the token exchange [6](#0-5) . No validation confirms the host is a real GitHub Enterprise instance before this exchange occurs — existing guards (`isDotCom`, `isGHE`) are exact-match and safe, but the flawed fallback in `isGitHubHost` bypasses them, exactly mirroring the original bug pattern of an incorrect/insufficiently-validated address reference being trusted downstream without the constructor-style validation the Sherlock fix recommended.

### Impact Explanation
The bundled Desktop OAuth `client_id`/`client_secret` (`app/app-info.ts`) would be sent to an attacker-controlled server whenever the user goes through an "Enterprise" sign-in prompt triggered against a spoofed host [7](#0-6) . While the dev secret is explicitly a public test credential, in production builds the replacement mechanism embeds the real distributed OAuth secret via `__OAUTH_SECRET__` [8](#0-7) , so this is a genuine secret-exfiltration primitive, not merely a UX bug. It also causes Desktop to treat a completely attacker-owned domain as a legitimate enterprise-classified endpoint, which corrupts the trust decision that determines how git credentials are subsequently resolved for that remote.

### Likelihood Explanation
Triggering `getEndpointKind`'s fallback path only requires the user to interact with a git remote hosted on a domain the attacker can register cheaply, e.g. `github.<attacker-tld>` or any subdomain containing `.github.` — no admin rights, malware, or leaked credentials needed, satisfying the "attacker controls a git remote" valid-impact criterion. The prompt-to-sign-in step does require the user to proceed through a natural Desktop UI flow (an expected "sign in to continue" prompt during clone/fetch/push), which is a normal, in-band interaction rather than an unnatural manual step.

### Recommendation
Replace the substring/regex heuristic in `isGitHubHost` with a proper registrable-domain (eTLD+1) comparison, or require the network `/meta` verification (already implemented later in the function) as the sole trust signal rather than a fallback that only runs after the loose regex already returned `true`. At minimum, do not treat regex-string matches as more authoritative than the live `x-github-request-id` verification, and consider blocking OAuth client secret transmission entirely to any endpoint that hasn't passed the strict `isDotCom`/`isGHE` checks or an explicitly user-configured Enterprise endpoint.

### Proof of Concept
1. Attacker registers `github.attacker.com` and stands up a git server plus a page mimicking a GitHub Enterprise OAuth authorize/token endpoint.
2. Attacker sends the victim a repository/remote URL such as `https://github.attacker.com/foo/bar.git` (e.g., as a clone link or added remote).
3. Victim clones/fetches in GitHub Desktop; the git credential trampoline calls `getEndpointKind`, which falls through to `isGitHubHost('https://github.attacker.com')`.
4. The regex `/(^|\.)(github)\./` matches `github.attacker.com` (matches at position 0), so `isGitHubHost` returns `true` and `getEndpointKind` returns `'enterprise'` [1](#0-0) [3](#0-2) .
5. Desktop prompts the user to sign in to this "enterprise" endpoint; browser-based OAuth is initiated with `urlBase = getHTMLURL('https://github.attacker.com')`, which resolves to the attacker's own host.
6. On callback, `requestOAuthToken` POSTs `client_id` and `client_secret` to `https://github.attacker.com/login/oauth/access_token`, exfiltrating Desktop's OAuth secret to the attacker [6](#0-5) .

### Citations

**File:** app/src/lib/api.ts (L132-139)
```typescript
const ClientID = process.env.TEST_ENV ? '' : __OAUTH_CLIENT_ID__
const ClientSecret = process.env.TEST_ENV ? '' : __OAUTH_SECRET__

if (!ClientID || !ClientID.length || !ClientSecret || !ClientSecret.length) {
  log.warn(
    `DESKTOP_OAUTH_CLIENT_ID and/or DESKTOP_OAUTH_CLIENT_SECRET is undefined. You won't be able to authenticate new users.`
  )
}
```

**File:** app/src/lib/api.ts (L2357-2368)
```typescript
export function getOAuthAuthorizationURL(
  endpoint: string,
  state: string
): string {
  const urlBase = getHTMLURL(endpoint)
  const scope = encodeURIComponent(oauthScopes.join(' '))

  return new window.URL(
    `/login/oauth/authorize?client_id=${ClientID}&scope=${scope}&state=${state}`,
    urlBase
  ).toString()
}
```

**File:** app/src/lib/api.ts (L2370-2395)
```typescript
export async function requestOAuthToken(
  endpoint: string,
  code: string
): Promise<string | null> {
  try {
    const urlBase = getHTMLURL(endpoint)
    const response = await request(
      urlBase,
      null,
      'POST',
      'login/oauth/access_token',
      {
        client_id: ClientID,
        client_secret: ClientSecret,
        code: code,
      }
    )
    tryUpdateEndpointVersionFromResponse(endpoint, response)

    const result = await parsedResponse<IAPIAccessToken>(response)
    return result.access_token
  } catch (e) {
    log.warn(`requestOAuthToken: failed with endpoint ${endpoint}`, e)
    return null
  }
}
```

**File:** app/src/lib/api.ts (L2435-2464)
```typescript
export async function isGitHubHost(url: string) {
  const { hostname } = new window.URL(url)

  const endpoint =
    hostname === 'github.com' || hostname === 'api.github.com'
      ? getDotComAPIEndpoint()
      : getEnterpriseAPIURL(url)

  if (isDotCom(endpoint) || isGHE(endpoint)) {
    return true
  }

  if (isKnownThirdPartyHost(hostname)) {
    return false
  }

  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
  }

  // bitbucket.example.com, etc
  if (/(^|\.)(bitbucket|gitlab)\./.test(hostname)) {
    return false
  }

  if (getEndpointVersion(endpoint) !== null) {
    return true
  }

```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L107-125)
```typescript
  // If it appears as if the endpoint is a GitHub host and we don't have an
  // account for that endpoint then we should prompt the user to sign in.
  if (
    endpointKind !== 'generic' &&
    !accounts.some(a => a.endpoint === apiEndpoint)
  ) {
    if (getIsBackgroundTaskEnvironment(token)) {
      debug('background task environment, skipping prompt')
      return undefined
    }

    const account = await ui.promptForGitHubSignIn(endpoint)

    if (!account) {
      setHasRejectedCredentialsForEndpoint(token, endpoint)
    }

    return credWithAccount(cred, account)
  }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L172-179)
```typescript
  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
}
```

**File:** app/app-info.ts (L5-22)
```typescript
const devClientId = '3a723b10ac5575cc5bb9'
const devClientSecret = '22c34d87789a365981ed921352a7b9a8c3f69d54'

const channel = getChannel()

const s = JSON.stringify

const optionalStringReplacement = (value: string | undefined) =>
  value === undefined || value.length === 0 ? 'undefined' : s(value)

export function getReplacements() {
  const isDevBuild = channel === 'development'

  return {
    __OAUTH_CLIENT_ID__: s(process.env.DESKTOP_OAUTH_CLIENT_ID || devClientId),
    __OAUTH_SECRET__: s(
      process.env.DESKTOP_OAUTH_CLIENT_SECRET || devClientSecret
    ),
```
