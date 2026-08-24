### Title
Forged `WWW-Authenticate` realm header from a remote git server bypasses GitHub-host verification, causing GitHub Desktop to treat an arbitrary attacker endpoint as a trusted GitHub/Enterprise account - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind` in the trampoline credential helper is supposed to classify a Git credential request as `github.com`, `enterprise`, or `generic` before Desktop decides how to authenticate it. One of its checks trusts a `wwwauth[]` header value that Git forwards verbatim from the remote server's HTTP response, letting any attacker-controlled remote spoof a `realm="GitHub"` challenge and be classified as an `enterprise` GitHub endpoint — skipping the real network-based verification (`isGitHubHost`) that exists specifically to prevent this.

### Finding Description
`getEndpointKind` runs these checks in order: [1](#0-0) 

Steps 1–3 (`isGist`, `isDotCom`, `isGHE`) are strict hostname allow-lists [2](#0-1) , which are safe. But step 4 iterates the credential map's `wwwauth[...]` entries and, if any value contains `realm="GitHub"`, immediately returns `'enterprise'` — **before** the only real verification step, `isGitHubHost(endpoint)` (an actual network probe, step 7), ever runs: [3](#0-2) 

The `wwwauth[]` values originate from the `WWW-Authenticate` header Git receives on an HTTP 401 from whatever server it is talking to, and are passed to the credential helper unmodified by Git's credential protocol (as the code's own comment confirms: "We use them as a happy-path to determine if the host is a GitHub host without having to resort to making a request ourselves"). This value is fully controlled by the remote server — i.e., by an attacker who owns/controls the git remote or a MITM-capable proxy the user has configured — with no cryptographic binding to the actual identity of the host.

Once `endpointKind` is forced to `'enterprise'`, `getCredential` skips the generic username/password credential path and instead invokes the "Sign in to GitHub" trampoline UI flow for an arbitrary, attacker-chosen origin: [4](#0-3) [5](#0-4) 

This is the same "official" GitHub Enterprise sign-in dialog used for genuine enterprise accounts (`dispatcher.beginEnterpriseSignIn` / `dispatcher.setSignInEndpoint`), rather than the plain generic-authentication dialog. If the user completes it, the resulting `Account` (login + token) is bound into the `AccountsStore` as if it were a legitimate GitHub Enterprise account for that attacker-controlled origin.

### Impact Explanation
This breaks the invariant that "GitHub-branded" authentication only occurs for hosts Desktop has verified as genuinely running GitHub software. By forging one HTTP header value, an attacker who controls a git remote (added by the user via a normal `git remote add`/`clone`/`fetch` flow — no special local access needed) can:
- Force Desktop to present its trusted "Sign in to GitHub" UI for an unrelated, attacker-controlled host (misleading trust indicator).
- Cause an unauthorized "account binding": a non-GitHub host gets registered in `AccountsStore` as a full GitHub Enterprise account, satisfying the listed valid-impact category of "unauthorized OAuth or account binding."
- Once bound, `findGitHubTrampolineAccount` will match future authentication requests for that same origin by URL origin comparison and silently hand back the stored token via `credWithAccount`, so credentials the user thought they gave once are now auto-supplied by Desktop on every subsequent operation against that host, and the endpoint may subsequently be treated as a first-class connected account throughout the UI (repository publishing targets, PR/issue integrations, etc.), widening the attack surface for further abuse.

This is Medium-ish severity: it does not directly leak an existing legitimate GitHub.com token (that would require the attacker to already be the origin of an existing bound account, which they cannot spoof for a real github.com/ghe.com host due to the strict allow-list checks earlier in the same function), but it does corrupt Desktop's trust classification for arbitrary attacker-supplied endpoints and results in unauthorized account binding plus misleading trust UI.

### Likelihood Explanation
Any git server the victim adds as a remote (a common and expected action — cloning/adding an attacker-shared repository, as required by the "Valid Impact" scope) can trivially return a 401 with a custom `WWW-Authenticate: Basic realm="GitHub"` header during an HTTPS auth challenge; Git forwards this to the configured credential helper unmodified. No user interaction beyond a normal fetch/push against the attacker's remote is required to trigger the misclassification itself; the follow-on account-binding step still requires the user to proceed through the sign-in prompt, but the prompt is disguised as Desktop's standard GitHub UI, making it more convincing than the generic-credential dialog it should have shown instead.

### Recommendation
- Do not let the `wwwauth[]` `realm="GitHub"` heuristic short-circuit classification into `'enterprise'`/`'github.com'` on its own; treat it only as a weak hint that still requires the existing `isGitHubHost(endpoint)` network verification (step 7) before trusting the host, or move the check after the verified network probe.
- When falling back to the "happy path" heuristic, surface the actual hostname prominently in the sign-in dialog (not just implied GitHub branding) so users can tell they're not authenticating to a verified GitHub instance.
- Consider gating `'enterprise'` classification strictly behind `isGitHubHost` verification results, using the `wwwauth` hint only to short-circuit the negative case (`realm` indicating a known non-GitHub provider → `'generic'`), never the positive `'enterprise'` case.

### Proof of Concept
1. Attacker hosts a git-over-HTTPS server (e.g., `https://evil.example.com/repo.git`) and shares it with the victim as a normal remote to clone/fetch/push.
2. Victim adds it as a remote in GitHub Desktop and performs an operation requiring authentication (fetch/push).
3. Git sends the HTTPS request; attacker's server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git invokes Desktop's credential helper (`git-credential-desktop`) with `wwwauth[0]=Basic realm="GitHub"` in the input.
5. `getEndpointKind` (trampoline-credential-helper.ts:157-165) matches the forged realm and returns `'enterprise'` without ever calling `isGitHubHost('https://evil.example.com')`.
6. `getCredential` finds no existing account bound to `evil.example.com` and calls `ui.promptForGitHubSignIn('https://evil.example.com')`, showing Desktop's standard "Sign in to GitHub Enterprise" dialog for the attacker's domain.
7. If the victim completes sign-in, an `Account` bound to `evil.example.com` is persisted; future fetch/push to that remote silently reuses the stored credential via `findGitHubTrampolineAccount`/`credWithAccount`.

Uncertainty: I could not trace the full downstream `AccountsStore`/OAuth implementation used by `dispatcher.beginEnterpriseSignIn` in this index to confirm exactly which credential material (basic auth vs. OAuth token) gets persisted, since those files were not returned by search; a Devin session with full repo access would be needed to verify the exact persisted token type and whether any additional server-identity check occurs during that sign-in flow.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-125)
```typescript
/** Implementation of the 'get' git credential helper command */
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-178)
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
```

**File:** app/src/lib/endpoint-capabilities.ts (L47-62)
```typescript
export const isDotCom = (ep: string) => {
  if (ep === getDotComAPIEndpoint()) {
    return true
  }

  const { hostname } = new URL(ep)
  return hostname === 'api.github.com' || hostname === 'github.com'
}

export const isGist = (ep: string) => {
  const { hostname } = new URL(ep)
  return hostname === 'gist.github.com' || hostname === 'gist.ghe.io'
}

/** Whether or not the given endpoint URI is under the ghe.com domain */
export const isGHE = (ep: string) => new URL(ep).hostname.endsWith('.ghe.com')
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
