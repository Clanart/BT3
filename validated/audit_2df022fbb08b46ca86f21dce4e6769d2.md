### Title
Spoofed `WWW-Authenticate: realm="GitHub"` Header From Any Git Remote Triggers a Native "Sign in to GitHub Enterprise" Prompt Bound to the Attacker's Host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop's credential-helper trampoline classifies an unknown, unauthenticated git remote as a legitimate "GitHub Enterprise" endpoint purely based on an attacker-controlled `WWW-Authenticate` HTTP header returned during a git network operation (clone, fetch, submodule update, LFS smudge, etc.). This is analogous to the PoH V1/V2 report's core flaw: a state/classification decision is derived from data the caller fully controls (the attacker's HTTP response) instead of from a verified, protocol-anchored source, letting the attacker walk the victim into a privileged flow (there: bypassing vouching; here: a native "Sign in to GitHub Enterprise" dialog) that was only meant to be reachable for genuinely-vetted hosts.

### Finding Description
`getEndpointKind` in [1](#0-0)  determines whether a credential request belongs to `github.com`, `ghe.com`, an "enterprise" GHES instance, or a "generic" (non-GitHub) host. The `dotcom`/`ghe`/`gist` checks rely on fixed hostnames, but for any other host it falls back to inspecting the raw credential fields Git captured from the HTTP response: [2](#0-1) 

These `wwwauth[...]` values come directly from the `WWW-Authenticate` header of whatever server answered the git network request — fully attacker-controlled if that server is a malicious/compromised remote, a corporate/transparent proxy, or a MITM-able endpoint reached via a submodule, LFS pointer, or redirect embedded in a cloned/fetched repository. If the header contains `realm="GitHub"`, the function returns `'enterprise'` with no further verification (no TLS pinning, no probe of `/meta` or any GitHub-specific API, nothing beyond this string).

That classification then drives `getCredential`: [3](#0-2) 

Because the attacker's host is not `github.com`, `ghe.com`, or any endpoint already bound to a stored `Account`, the `accounts.some(a => a.endpoint === apiEndpoint)` check fails, and Desktop calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` equal to the attacker's own origin.

`promptForGitHubSignIn` then binds the entire sign-in flow to that attacker-controlled origin: [4](#0-3) 

Since the hostname is not `github.com`, this takes the `beginEnterpriseSignIn` branch and calls `dispatcher.setSignInEndpoint(origin)` with the attacker's origin, then shows the native `SignIn` popup. Any Personal Access Token or OAuth code the user subsequently enters in that popup is sent to whatever API the sign-in store resolves from that attacker-supplied origin — i.e., directly to the attacker's server — not to the user's real GitHub Enterprise Server.

The broken invariant, mirroring the report: a classification/lookup value (`humanityId` there, `endpointKind`/sign-in `endpoint` here) that should only ever be populated from a trusted, verified source is instead derived from attacker-supplied input, and downstream privileged logic (bypassing the vouch/challenge flow there; opening a credentialed enterprise sign-in flow bound to an arbitrary origin here) treats that value as trustworthy.

### Impact Explanation
This lets any attacker who controls a cloned/fetched repository's remote content (submodule URL, LFS server, redirect target) or who can respond to a git HTTP request (malicious/compromised proxy) impersonate "GitHub Enterprise" to the victim without owning or spoofing any real GitHub domain. The victim is shown Desktop's own native, trusted-looking sign-in UI, but any PAT or OAuth authorization the user grants is delivered to the attacker's endpoint — a direct credential/token exfiltration and unauthorized account-binding primitive, matching the "credential/token exfiltration" and "unauthorized OAuth or account binding" categories in scope.

### Likelihood Explanation
The trigger requires no special privileges: opening/cloning/fetching a repository containing a submodule or LFS pointer to an attacker-controlled HTTPS URL, or performing any git network operation while behind an attacker-influenced proxy, is enough to cause Git to surface a 401 response with a crafted `WWW-Authenticate` header, which Desktop's trampoline picks up unconditionally on the "no direct GitHub host match" path. No user action beyond the normal act of fetching/cloning is required to reach the vulnerable branch; the only remaining step needed for the attacker to gain value is the user completing the resulting sign-in prompt, which Desktop presents as if it were a legitimate first-party flow.

### Recommendation
Do not classify a host as `enterprise`/GitHub-capable based solely on the unauthenticated `WWW-Authenticate` header. Require the same verification already available via `isGitHubHost(endpoint)` (an authenticated network probe to a `/meta`-style endpoint) before offering the enterprise sign-in path, or at minimum surface the exact attacker-controlled hostname prominently in the `SignIn` popup and require explicit user confirmation that they intend to add a new enterprise account for that specific, unfamiliar host — separate from the implicit "this looks like GitHub" heuristic.

### Proof of Concept
1. Publish/host a git-served repository (or a malicious LFS/API endpoint referenced by a submodule/`.lfsconfig` in an otherwise normal-looking repository) at `https://attacker.example`.
2. Configure the server so that unauthenticated requests receive `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
3. Victim opens GitHub Desktop and clones/fetches the repository (or a repository containing a submodule pointing at `attacker.example`).
4. Git invokes Desktop's credential helper trampoline for `https://attacker.example`; `getEndpointKind` returns `'enterprise'` per [5](#0-4) .
5. `getCredential` finds no stored account for `attacker.example` and calls `ui.promptForGitHubSignIn('https://attacker.example')` [6](#0-5) .
6. Desktop shows its native "Sign in to GitHub Enterprise" popup bound to `attacker.example` via `setSignInEndpoint(origin)` [7](#0-6) .
7. If the victim enters a PAT or completes OAuth, the credential is delivered to the attacker's server instead of a real GitHub Enterprise instance.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-125)
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
