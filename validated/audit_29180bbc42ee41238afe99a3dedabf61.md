### Title
Attacker-controlled `WWW-Authenticate` realm spoofs GitHub Enterprise sign-in prompt, redirecting OAuth sign-in flow to an untrusted host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The credential-helper trampoline decides whether an unknown host should be treated as a GitHub Enterprise (`enterprise`) endpoint by trusting a `WWW-Authenticate` `realm` value that Git forwards verbatim from the remote server's HTTP response. A malicious or compromised remote/proxy can set `realm="GitHub"` on its `401 WWW-Authenticate` header to force Desktop to classify the host as `enterprise`, which triggers `promptForGitHubSignIn` and opens the full GitHub Enterprise OAuth sign-in flow pointed at the attacker's origin.

### Finding Description
`getEndpointKind` in [1](#0-0)  determines how a credential request should be routed. Before falling back to network-based host discovery (`isGitHubHost`), it inspects the `wwwauth[...]` credential entries that Git populates from the server's HTTP response headers: [2](#0-1) 

If any `wwwauth[n]` value contains `realm="GitHub"`, the function immediately returns `'enterprise'` — no further verification (no request to `/meta`, no check of `isGHE`/`isGHES`, no TLS/certificate validation) is performed. This value originates entirely from the remote server's response and is not authenticated in any way; any git host (including a malicious or MITM proxy responding to a `git fetch/clone/push` over HTTP(S)) can set this header.

Back in `getCredential`, when `endpointKind !== 'generic'` and no existing account matches the endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [3](#0-2) 

`promptForGitHubSignIn` in [4](#0-3)  then unconditionally begins an Enterprise sign-in flow scoped to the attacker-controlled `origin`: [5](#0-4) 

The broken invariant is the same class as in the external report: code assumes an untrusted, attacker-influenced input ("this looks like a GitHub host based on a header value") is equivalent to a verified trust decision ("this is actually GitHub/GHE, safe to run the OAuth flow against"), without validating that assumption before acting irreversibly. Just as the reward pool assumed all "reward tokens" are the deposit asset without checking, Desktop assumes the `realm="GitHub"` string in an HTTP response actually indicates a real GitHub Enterprise host.

### Impact Explanation
This falls into the "unauthorized OAuth or account binding" category of valid impact: an attacker who controls the git remote/proxy response (e.g., a malicious clone/fetch host, a compromised network proxy, or MITM on an HTTP remote) can cause Desktop to present the user with a GitHub Enterprise "sign in" popup bound to the attacker's origin instead of failing safe as a generic/unknown credential prompt. Because the enterprise sign-in flow performs OAuth/device-flow authentication against whatever host is passed as `endpoint`, this can be used to phish users into authenticating against an attacker-hosted look-alike GHE OAuth endpoint, or to bind a legitimate-looking "enterprise account" entry in Desktop to a server the attacker controls — all without the user manually typing or approving anything beyond normal credential prompts they'd expect during a clone/fetch. This does not require local access, admin rights, or pre-existing malware — only that the user performs a normal Git network operation (clone/fetch/push) against a repository/remote/proxy the attacker controls.

### Likelihood Explanation
Likelihood is moderate: the attacker only needs to control the HTTP(S) endpoint Git talks to (a plausible scenario for "clone a URL a user was sent," or a malicious/compromised corporate proxy) and needs to emit a `WWW-Authenticate: Basic realm="GitHub"` (or any header containing the substring `realm="GitHub"`) on a 401 response. Git itself forwards these header values to the credential helper (`wwwauth[]` entries) as designed, so no protocol-level exploitation is required — this is a documented, intended data path being trusted without verification. The only mitigating factor is that the resulting popup is visually a "Sign in to GitHub Enterprise" dialog scoped to a URL the user may notice, so success depends on the user not verifying the domain shown.

### Recommendation
Do not trust the `WWW-Authenticate` realm value alone to decide `enterprise` classification. Either:
- Use it only as a hint to prioritize/order the existing `isGitHubHost()` network verification (already implemented) rather than as a terminal decision, or
- Require confirmation via `isGitHubHost()` / a real `/meta` (or equivalent) check before classifying a previously-unknown host as `enterprise` and launching the OAuth sign-in flow, and/or
- Surface the resolved hostname prominently in the sign-in dialog and require explicit user confirmation before starting an Enterprise OAuth flow for a host that has no prior associated account.

### Proof of Concept
1. Attacker stands up an HTTP(S) git server (or a MITM/compromised proxy) at `https://evil.example.com/victim/repo.git`.
2. User clones or fetches from this URL in GitHub Desktop.
3. Git performs the request; the server responds `401 Unauthorized` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git invokes the `git-credential-desktop` trampoline `get` command with `wwwauth[]=Basic realm="GitHub"` in stdin.
5. `getEndpointKind` matches the regex `realm="GitHub"` and returns `'enterprise'` without any network verification: [6](#0-5) 
6. Since no account exists for `evil.example.com`, `getCredential` calls `ui.promptForGitHubSignIn('https://evil.example.com')`, which opens a "Sign in to GitHub Enterprise" dialog scoped to that attacker-controlled origin: [7](#0-6) 
7. The user, believing this is a legitimate GHE authentication prompt, completes the OAuth/device flow against the attacker's server, allowing credential/token exfiltration or a spoofed account binding.

Note: I was not able to trace the full downstream OAuth/device-flow implementation (`beginEnterpriseSignIn`/`setSignInEndpoint`) within the indexed portion of the codebase to confirm whether any additional host-validation occurs later in that flow; if such validation exists downstream, it would reduce (but not eliminate, since the popup itself already presents a plausible-looking enterprise sign-in UI) the severity of this issue. Given index size limits, a full Devin session may be needed to inspect the complete sign-in store code path (`app-store.ts` `_beginEnterpriseSignIn`, `SignInStore`) for confirmation.

### Citations

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-179)
```typescript
const getEndpointKind = async (cred: Credential, store: Store) => {
  const credentialUrl = getCredentialUrl(cred)
  const endpoint = `${credentialUrl}`

  if (isGist(endpoint)) {
    return 'generic'
  }

  if (isDotCom(endpoint)) {
    return 'github.com'
  }

  if (isGHE(endpoint)) {
    return 'ghe.com'
  }

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

  const existingAccount = await findGitHubTrampolineAccount(store, endpoint)
  if (existingAccount) {
    return isDotCom(existingAccount.endpoint) ? 'github.com' : 'enterprise'
  }

  // All GitHub hosts use HTTPS, so if the protocol is not HTTPS we can
  // assume that this is not a GitHub host.
  if (credentialUrl.protocol !== 'https:') {
    return 'generic'
  }

  return (await isGitHubHost(endpoint)) ? 'enterprise' : 'generic'
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
