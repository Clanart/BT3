## Analysis

The Celo bug's broken invariant: a value that other parties rely on for trust decisions (commission split) can be altered by an untrusted/self-interested party immediately before a critical event, with the receiving code applying that attacker-influenced value without independent verification or delay. The strongest Desktop analog is in the credential-helper's host-classification logic, where a value taken directly from an untrusted network response (`WWW-Authenticate` realm) is used, ahead of any authoritative check, to decide whether to launch a GitHub OAuth sign-in flow.

### Title
Attacker-Controlled `WWW-Authenticate` Realm Spoofs GitHub Host Classification and Triggers OAuth Sign-In Prompt for Arbitrary Endpoints - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
`getEndpointKind` in the trampoline credential helper classifies a remote endpoint as `github.com`, `enterprise`, or `generic` before deciding how to source credentials. Before making any authoritative determination (contacting the real API, or matching against a known account), it trusts a `wwwauth[]` credential field — which is populated directly from the HTTP `WWW-Authenticate` response header returned by whatever server Git connected to — and treats any response containing `realm="GitHub"` as proof the host is an "enterprise" GitHub endpoint.

### Finding Description
`getCredential` first checks for a stored GitHub account and, failing that, calls `getEndpointKind`: [1](#0-0) 

This loop iterates the credential map entries git passes to its credential helper. Git populates `wwwauth[]` fields directly from the server's `WWW-Authenticate` HTTP header on the actual remote/proxy connection (per the code comment: *"Git captures any WWW-Authenticate headers and forwards them to the credential helper"*). Any git remote or HTTP proxy the user's repository points to — fully attacker-controlled content, since it's just an HTTP response header — can set `WWW-Authenticate: Basic realm="GitHub"` and the function immediately returns `'enterprise'`, with **no verification against the real GitHub API** (the authoritative `isGitHubHost` network check at the bottom of the function is only reached if no `wwwauth[]` header matched).

That classification feeds directly into `getCredential`: [2](#0-1) 

Because `endpointKind !== 'generic'` and the user has no stored account for this arbitrary attacker endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, which invokes `beginEnterpriseSignIn`/`beginDotComSignIn` and shows the `SignIn` popup, using the attacker-supplied `endpoint` (the raw credential URL, i.e. the remote's host) as the sign-in target: [3](#0-2) 

This is the exact bug-class analog: a value under an untrusted party's control (the `WWW-Authenticate` realm string returned by a remote/proxy) is consumed *without a delay, second confirmation, or authoritative cross-check* to make a security-relevant decision (whether to treat a host as a "GitHub" endpoint and solicit sign-in), exactly as the original Celo commission value was consumed without a lock/queue before being used to redistribute rewards.

### Impact Explanation
If a user adds/clones from a malicious or compromised git remote (or one served through a malicious HTTP proxy — e.g. `http.proxy` on a shared network), that remote/proxy can return a 401 response with `WWW-Authenticate: realm="GitHub"` on any push/fetch. Desktop will present its native GitHub sign-in dialog for that attacker-chosen `endpoint` (an arbitrary host), which is exactly the flow used to bind OAuth accounts / GHE PATs in Desktop's `SignInStep.EndpointEntry` → `Authentication` flow. A user who doesn't scrutinize the endpoint value shown in the dialog can be led to authenticate against, or leak enterprise host trust decisions to, a domain they did not intend — an unauthorized OAuth/account-binding scenario, and a step toward credential exfiltration if combined with a spoofed enterprise login page reachable at that host.

### Likelihood Explanation
The `wwwauth[]` short-circuit is explicitly a "happy path" optimization specifically to *avoid* the authoritative `isGitHubHost` check, meaning this path is hit routinely, not just as a fallback — any 401 response from a remote with that header will take it. Setting a custom `WWW-Authenticate` header on an HTTP git server or MITM-capable proxy requires no special git craftiness beyond controlling the endpoint the user's repository is configured to fetch/push against, which matches the "attacker controls...a git remote/proxy response" criterion.

### Recommendation
Do not trust the `wwwauth[]` realm string alone to classify a host as GitHub/enterprise for the purposes of triggering the sign-in flow. At minimum, require the `isGitHubHost` authoritative check (or an existing-account origin match) before offering a GitHub-branded sign-in prompt, and always display the exact remote host to the user in the sign-in dialog with clear provenance (i.e., "this host claims to be a GitHub instance because it said so") rather than implicitly trusting the header for UX routing decisions.

### Proof of Concept
1. Stand up an HTTP server (or MITM proxy) at `https://evil.example.com`.
2. Configure it to return `401 Unauthorized` with header `WWW-Authenticate: Basic realm="GitHub"` for any git-over-HTTP request.
3. In GitHub Desktop, add a remote/clone pointing at `https://evil.example.com/foo/bar.git` with no account configured for that endpoint.
4. Trigger a fetch/push; Git captures the header and forwards `wwwauth[0]=Basic realm="GitHub"` to Desktop's credential helper.
5. `getEndpointKind` returns `'enterprise'` without contacting the real GitHub API; `getCredential` sees no matching stored account and calls `promptForGitHubSignIn('https://evil.example.com/...')`, which opens Desktop's native "Sign in to GitHub Enterprise" dialog pre-populated with the attacker's endpoint.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-165)
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
