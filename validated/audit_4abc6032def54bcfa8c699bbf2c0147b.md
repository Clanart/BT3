## Analysis

The VPN report's broken invariant is: **a classification decision that determines whether traffic is treated as "trusted/local" is made from attacker-controllable data, without an authoritative check, and that misclassification routes sensitive traffic outside the protected channel.**

The closest analog in this codebase is the heuristic host-trust check `isGitHubHost()` in `app/src/lib/api.ts`, which is used by the git credential helper to decide whether an arbitrary remote endpoint should be treated as a "trusted GitHub Enterprise" server — a decision that gates the Enterprise **OAuth sign-in** flow (which transmits GitHub Desktop's static `ClientID`/`ClientSecret`).

### Title
Naive hostname substring heuristic lets an attacker-controlled git remote be misclassified as a trusted GitHub Enterprise host, triggering the Enterprise OAuth flow against the attacker's server - (File: `app/src/lib/api.ts`)

### Summary
`isGitHubHost()` short-circuits its own authoritative verification (a `/meta` HTTPS probe checking for the `x-github-request-id` header) whenever a hostname merely *contains* the substring pattern `github.`, treating it as a legitimate GitHub Enterprise host without ever contacting it. Since the hostname comes directly from a git remote URL — fully attacker-controlled — this "trust by pattern" check can be satisfied by any domain the attacker owns (e.g. `github.attacker.example` or `foo.github.attacker.example`), exactly as the VPN bug let an attacker choose a subnet to fool the client's local/non-local classification.

### Finding Description
`isGitHubHost()` performs the following checks, in order, before doing any real network verification: [1](#0-0) 

The regex `/(^|\.)(github)\./` matches on hostname *substring shape only* — it has no relationship to DNS ownership or actual GitHub infrastructure. Any attacker who registers or controls a domain containing `github.` as a label boundary (e.g. `github.example.net`, `sub.github.example.net`) will short-circuit to `return true` before the safe `/meta` request (which requires an actual `x-github-request-id` response header from the real server) is ever attempted.

This function feeds directly into the git credential helper's endpoint-kind decision: [2](#0-1) 

When `getEndpointKind` returns `'enterprise'` for such a host and no existing account is bound to it, `getCredential` invokes the sign-in prompt for that attacker-controlled endpoint: [3](#0-2) 

The UI helper wires this straight into the standard "Enterprise sign in" flow, associating the sign-in session with the attacker's endpoint: [4](#0-3) 

If the user proceeds with "Sign in using your browser," `requestOAuthToken` performs the authorization-code exchange **against the attacker-controlled endpoint**, sending Desktop's static Enterprise OAuth `client_id`/`client_secret`: [5](#0-4) 

Both `getOAuthAuthorizationURL` and `requestOAuthToken` derive `urlBase` from `getHTMLURL(endpoint)`, where `endpoint` is the attacker's own hostname — there is no verification (beyond the flawed substring heuristic) that this endpoint is a real GitHub Enterprise server.

### Impact Explanation
This corresponds to the "unauthorized OAuth or account binding" and "credential/token exfiltration" categories in scope: the app's static Enterprise OAuth `ClientID`/`ClientSecret` are transmitted to a host the attacker fully controls simply by virtue of git needing credentials for that remote (a normal, expected step when fetching/cloning from that remote — no extraneous user action beyond the credential-prompt flow that Desktop itself already surfaces). Any legitimate GHE OAuth client secret leak allows further phishing/abuse of the OAuth flow. Additionally, the enterprise sign-in dialog is bound to `beginEnterpriseSignIn`/`setSignInEndpoint(origin)` for that attacker host, i.e., an account association ("account binding") that the attacker steered purely by choosing a domain name.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to control a domain containing the label `github.` (trivially obtainable, e.g. subdomains under an attacker-owned parent domain) and for the user to add that host as a remote and perform any git network operation that triggers credential negotiation (fetch/push/clone) while not already having an account bound to that exact endpoint. The `/meta` verification — the actual security control — is *never reached* in this scenario because of the early regex return, so existing hardening (the certificate-error-suppressed, `x-github-request-id`-gated probe) provides no protection here.

### Recommendation
Remove or tighten the substring-based fast paths in `isGitHubHost()` (`/(^|\.)(github)\./` and the negative bitbucket/gitlab heuristic) so that host trust is only ever established via exact matches against `isDotCom`/`isGHE`, an existing bound `Account`, or the authoritative `/meta` HTTP probe — never via loose hostname pattern matching. At minimum, gate the OAuth/Enterprise sign-in flow behind the successful authoritative check rather than the heuristic.

### Proof of Concept
1. Register/control a host such as `github.attacker.example` and serve a git repository over HTTPS there requiring authentication (any 401 without a WWW-Authenticate GitHub realm, so the heuristic path is reached).
2. In GitHub Desktop, add this as a remote and perform `git fetch`.
3. Git's credential helper calls into `trampoline-credential-helper.ts`; `getEndpointKind` calls `isGitHubHost('https://github.attacker.example')`.
4. `hostname = 'github.attacker.example'` matches `/(^|\.)(github)\./` — function returns `true` without ever contacting `github.attacker.example/meta`.
5. Desktop shows "Sign in to your GitHub Enterprise" bound to `github.attacker.example`; if the user proceeds via "Sign in using your browser," Desktop's static Enterprise `client_id`/`client_secret` are POSTed to `https://github.attacker.example/login/oauth/access_token` per `requestOAuthToken`. [6](#0-5)

### Citations

**File:** app/src/lib/api.ts (L2357-2395)
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

**File:** app/src/lib/api.ts (L2435-2454)
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

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-99)
```typescript
  public promptForGitHubSignIn(endpoint: string): Promise<Account | undefined> {
    return new Promise<Account | undefined>(async resolve => {
      const cb = (result: SignInResult) => {
        resolve(result.kind === 'success' ? result.account : undefined)
        this.dispatcher.closePopup(PopupType.SignIn)
      }

      const { hostname, origin } = new URL(endpoint)
      if (hostname === 'github.com') {
        this.dispatcher.beginDotComSignIn(cb)
      } else {
        this.dispatcher.beginEnterpriseSignIn(cb)
        await this.dispatcher.setSignInEndpoint(origin)
      }

      this.dispatcher.showPopup({
        type: PopupType.SignIn,
        isCredentialHelperSignIn: true,
        credentialHelperUrl: endpoint,
      })
```
