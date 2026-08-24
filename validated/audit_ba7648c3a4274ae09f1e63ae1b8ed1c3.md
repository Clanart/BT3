## Title
Naive substring hostname matching in `isGitHubHost()` lets an attacker-controlled domain be classified as a trusted GitHub Enterprise host, triggering a spoofed sign-in prompt that exfiltrates credentials/tokens - (File: `app/src/lib/api.ts`)

### Summary
The reported curl CVE is a "broken domain-boundary check" bug class: a security decision (blocking cookies on a bare TLD) is made using naive string matching on the hostname instead of correct domain-suffix semantics, and an attacker can slip past it by crafting a hostname that satisfies the loose pattern (`example.com.`) without being trusted. GitHub Desktop has the same bug class in its GitHub-host detection heuristic, `isGitHubHost()`, which is used by the git credential trampoline to decide whether to trust a remote host as GitHub/GitHub Enterprise and pop the built-in sign-in flow.

### Finding Description
`isGitHubHost()` in [1](#0-0)  classifies a hostname as a genuine "github.example.com" Enterprise host using the regex:

```js
// github.example.com,
if (/(^|\.)(github)\./.test(hostname)) {
  return true
}
```

This regex only checks that the substring `github.` appears at the start of the hostname or immediately after a dot — it does not require `github` to be a suffix-anchored, single label domain owned by GitHub. A hostname such as `foo.github.attacker.com` satisfies `(^|\.)(github)\.` (the `.github.` occurs right after `foo`), so it is misclassified as a trusted GitHub Enterprise host even though `attacker.com` is the actual registrable domain and is entirely attacker-controlled.

This classification is consumed directly by the git credential-helper trampoline. In `getEndpointKind()`, `isDotCom`/`isGHE` fail for the attacker host, the `wwwauth` heuristics don't match, and it falls through to the vulnerable check: [2](#0-1) 

```js
if (credentialUrl.protocol !== 'https:') {
  return 'generic'
}
return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
```

Because the attacker host is served over HTTPS, this returns `'enterprise'`. In `getCredential()`, since no account matches this bogus endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` instead of the safer "generic git credential" prompt: [3](#0-2) 

`promptForGitHubSignIn()` then launches Desktop's real GitHub sign-in UI against the attacker's endpoint: [4](#0-3) 

```js
const { hostname, origin } = new URL(endpoint)
if (hostname === 'github.com') {
  this.dispatcher.beginDotComSignIn(cb)
} else {
  this.dispatcher.beginEnterpriseSignIn(cb)
  await this.dispatcher.setSignInEndpoint(origin)
}
```

Since `hostname !== 'github.com'`, this begins the **Enterprise** sign-in flow with the attacker's `origin` as the endpoint — the same UI a user would see for a legitimate GHE instance, complete with OAuth-based or PAT-based authentication that talks directly to the attacker's server (`getOAuthAuthorizationURL`/`requestOAuthToken` build their URLs from `getHTMLURL(endpoint)`, i.e., the attacker's host, see [5](#0-4) ).

### Impact Explanation
An attacker who controls a repository the victim clones/fetches (e.g., via a submodule URL, or any git operation that causes Git to ask the trampoline credential helper for creds against `https://foo.github.attacker.com/...`) can cause Desktop to present its trusted "Sign in to GitHub Enterprise" dialog for a host it has silently mis-vetted as a real GitHub host. If the user completes that sign-in (OAuth or PAT), the resulting access token or OAuth authorization code is sent to the attacker-controlled server, and Desktop will store an `Account` bound to the attacker's endpoint as if it were a legitimate enterprise account. This is a credential/token-exfiltration and unauthorized-account-binding primitive triggered purely by content the attacker supplies in a cloned/fetched repository.

### Likelihood Explanation
The trigger condition is a hostname the attacker fully controls (e.g. `sub.github.<attacker-domain>`), reachable via any git remote/submodule the victim clones or fetches — no admin rights, local access, or prior compromise needed. The credential prompt appears as part of Desktop's normal "authenticate to access this remote" UX, so the only "unnatural" step is the victim providing GitHub credentials when prompted, which is expected behavior when Desktop needs auth for a private-looking repo it (incorrectly) believes is GitHub Enterprise.

### Recommendation
Replace the substring regex in `isGitHubHost()` (`app/src/lib/api.ts:2452`) with a proper right-anchored label check (e.g., `hostname === 'github.<tld>'` is meaningless generically, but at minimum require the match to be anchored to the end of the hostname, such as `/(^|\.)github\.[^.]+$/` only after excluding known non-GitHub multi-label domains), or better, drop heuristic string matching in favor of the existing `/meta` HTTP-based verification (`x-github-request-id` check) as the sole source of truth before ever classifying an unknown host as `'enterprise'` and invoking `promptForGitHubSignIn`.

### Proof of Concept
1. Attacker registers `attacker.com` and stands up an HTTPS server responding at `sub.github.attacker.com`.
2. Attacker publishes a repository containing a `.gitmodules` entry (or otherwise gets the victim to add a remote) pointing at `https://sub.github.attacker.com/owner/repo.git`.
3. Victim clones/fetches/updates submodules in GitHub Desktop; Git invokes the credential trampoline for `https://sub.github.attacker.com`.
4. `isDotCom`/`isGHE`/`wwwauth` checks fail; `isGitHubHost()`'s regex `/(^|\.)(github)\./.test('sub.github.attacker.com')` matches, returning `true`.
5. `getEndpointKind()` returns `'enterprise'`; `getCredential()` finds no matching account and calls `ui.promptForGitHubSignIn('https://sub.github.attacker.com/...')`.
6. Desktop shows its native "Sign in to GitHub Enterprise" dialog against `sub.github.attacker.com`. If the victim signs in, credentials/OAuth code go straight to the attacker's server.

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

**File:** app/src/lib/api.ts (L2451-2454)
```typescript
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
