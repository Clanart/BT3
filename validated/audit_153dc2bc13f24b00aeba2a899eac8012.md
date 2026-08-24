This confirms the analog. `promptForGitHubSignIn(endpoint)` in `app/src/lib/trampoline/trampoline-ui-helper.ts:80-104` calls `dispatcher.setSignInEndpoint(origin)` and `dispatcher.beginEnterpriseSignIn(cb)`, driving the full GitHub Enterprise OAuth sign-in flow (using the real client ID/secret via `getOAuthAuthorizationURL`/`requestOAuthToken` in `app/src/lib/api.ts:2357-2395`) against whatever `endpoint`/`origin` was passed in — and that value is attacker-influenced, as shown below.

### Title
Attacker-forged `WWW-Authenticate` realm on a Git remote causes Desktop to treat an arbitrary host as GitHub Enterprise, triggering the OAuth Enterprise sign-in flow (client secret sent to attacker host) - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts:137-179` classifies the credential-request host as `'enterprise'` purely based on the `wwwauth[]` value Git forwarded from the remote server's `WWW-Authenticate` response header, without validating that the host is actually a GitHub Enterprise Server. This "fast path" bypasses the network-based `isGitHubHost()` sanity check entirely (`app/src/lib/api.ts:2435-2491`), analogous to the Sherlock finding where `ConvexBoosterController::canCall` trusted mismatched/attacker-influenced pool metadata instead of validating the actual token flow direction — here Desktop trusts attacker-suppliable response metadata instead of validating the actual host identity.

### Finding Description
Inside `getEndpointKind`: [1](#0-0) 

Any HTTP(S) server that a user's git operation talks to (a cloned/fetched repo's `origin`, a submodule remote, a git proxy in the middle, or a redirect target) can respond with a `401` and header `WWW-Authenticate: Basic realm="GitHub"` for a URL that has nothing to do with GitHub. Git's credential protocol captures this header and forwards it to Desktop's credential helper trampoline as a `wwwauth[N]=...` field, which `getEndpointKind` matches via a simple substring check `v.includes('realm="GitHub"')` — with no verification of the actual origin, TLS certificate authority, or a live network probe (the kind performed by `isGitHubHost()` later in the same function for the fallback case, at `app/src/lib/trampoline/trampoline-credential-helper.ts:178`).

Once classified as `'enterprise'`, `getCredential` in the same file (`app/src/lib/trampoline/trampoline-credential-helper.ts:94-135`) skips the "generic" credential path and, if no account is registered for that endpoint, calls `ui.promptForGitHubSignIn(endpoint)`. That function (`app/src/lib/trampoline/trampoline-ui-helper.ts:80-104`) unconditionally treats any non-`github.com` hostname as GitHub Enterprise and kicks off `dispatcher.beginEnterpriseSignIn` + `dispatcher.setSignInEndpoint(origin)`, wiring the full Enterprise OAuth authorization flow (`getOAuthAuthorizationURL` / `requestOAuthToken` in `app/src/lib/api.ts:2357-2395`) to the attacker-controlled `origin`.

The broken invariant mirrors the Sherlock bug precisely: a value meant to describe the far side of a two-sided relationship (the *token/role identity of the remote host*) is derived from data the counterparty fully controls, and used directly as an authorization/trust decision, exactly as the pool's swapped `lptoken`/reward token was used unchecked to authorize the call.

### Impact Explanation
If the user is subsequently coaxed (or auto-triggers, since existing GitHub Enterprise account holders on other hosts routinely get "sign in" prompts for new hosts) into completing the resulting Enterprise sign-in prompt, `requestOAuthToken` POSTs `client_id` and `client_secret` (Desktop's real OAuth application secret) to `${attacker-origin}/login/oauth/access_token`. This exfiltrates Desktop's OAuth client credentials to an attacker-controlled server and can be used to impersonate the Desktop OAuth app or phish the user for a real GitHub OAuth code under a UI that looks like a legitimate GitHub Enterprise sign-in (since `credentialHelperUrl`/`remoteUrl` shown in the popup is attacker's own domain, easily made to look plausible for internal GHE deployments).

### Likelihood Explanation
The attacker only needs to control (or man-in-the-middle) the HTTP response of a git remote the victim clones/fetches/pulls from — squarely within the allowed threat model ("git remote/proxy response" attacker). No local access, admin rights, or prior compromise is required; a single crafted `401` with a forged `WWW-Authenticate` header on any git operation is sufficient to flip the classification.

### Recommendation
Do not trust the `wwwauth[]` realm string alone to classify a host as GitHub/Enterprise. Require it to only be used as a hint that still needs to be corroborated with the same live-probe check (`isGitHubHost`) used in the fallback branch, or otherwise gate the Enterprise sign-in / OAuth flow behind an explicit user confirmation showing the true hostname and requiring it to match an existing/newly-verified Enterprise root before invoking `beginEnterpriseSignIn`.

### Proof of Concept
1. Attacker sets up a git-over-HTTP(S) server (or a MITM/compromised remote) at `https://evil.example.com/some/repo.git`.
2. Victim adds/clones this as a remote in GitHub Desktop and performs any fetch/push/pull that requires auth.
3. On the initial unauthenticated request, the attacker's server replies `401` with `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this via the credential helper protocol as `wwwauth[0]=Basic realm="GitHub"` to Desktop's trampoline (`app/src/lib/trampoline/trampoline-credential-helper.ts`).
5. `getEndpointKind` returns `'enterprise'` for `evil.example.com` without any network verification.
6. `getCredential` finds no matching account and calls `promptForGitHubSignIn('https://evil.example.com')`.
7. `promptForGitHubSignIn` calls `dispatcher.setSignInEndpoint('https://evil.example.com')` and starts the Enterprise OAuth flow; if the victim proceeds, `requestOAuthToken` sends the OAuth `client_id`/`client_secret` to `https://evil.example.com/login/oauth/access_token`. [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-135)
```typescript
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }

  const endpointKind = await getEndpointKind(cred, store)
  const accounts = await store.getAll()

  const endpoint = `${getCredentialUrl(cred)}`
  const apiEndpoint = getAPIEndpoint(endpoint)

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

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-165)
```typescript
  // When Git attempts to authenticate with a host it captures any
  // WWW-Authenticate headers and forwards them to the credential helper. We
  // use them as a happy-path to determine if the host is a GitHub host without
  // having to resort to making a request ourselves.
  for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
        return 'generic'
      }
    }
  }
```

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L80-104)
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
    }).catch(e => {
      log.error(`Could not prompt for GitHub sign in`, e)
      return undefined
    })
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
