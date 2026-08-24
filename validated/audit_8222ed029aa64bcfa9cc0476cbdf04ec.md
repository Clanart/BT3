## Title
Spoofable `WWW-Authenticate: realm="GitHub"` header lets a malicious HTTPS remote force the Enterprise sign-in flow (and PAT/OAuth submission) against an unauthorized host — (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`getEndpointKind` classifies a git credential-helper request as `github.com`, `ghe.com`, `enterprise`, or `generic`. For non-first-party hosts it uses the `wwwauth[]` entries that Git forwards from the server's `WWW-Authenticate` HTTP header as a "happy path" shortcut to decide a host is GitHub Enterprise, instead of doing the network-based verification (`isGitHubHost`) that is otherwise required.

### Finding Description
When Git performs an HTTPS request and receives a `401` with a `WWW-Authenticate` header, it captures the header verbatim and forwards it to Desktop's credential helper as `wwwauth[]=...` stdin fields [1](#0-0) . This header is entirely attacker-controlled content coming from the remote server response, while the credential/host itself (`getCredentialUrl(cred)`) is the actual, real remote host (e.g. an attacker-owned domain that is not `github.com`/`*.ghe.com`) [2](#0-1) .

`getEndpointKind` trusts this attacker-supplied header content directly:
```
if (v.includes('realm="GitHub"')) {
  return 'enterprise'
} else if (/realm="(GitLab|Gitea|Atlassian Bitbucket)"/.test(v)) {
  return 'generic'
}
``` [3](#0-2) 

Only if none of the `wwwauth[]` entries match does the code fall back to the legitimate, network-verified check `isGitHubHost(endpoint)` [4](#0-3) . This means a malicious server can bypass the actual GitHub-host verification simply by returning a `WWW-Authenticate` header containing `realm="GitHub"`.

Once `endpointKind !== 'generic'` and no existing account matches the endpoint, `getCredential` calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` equal to the attacker's own URL [5](#0-4) . `promptForGitHubSignIn` then starts the Enterprise sign-in flow against that attacker-controlled origin:
```
const { hostname, origin } = new URL(endpoint)
if (hostname === 'github.com') {
  this.dispatcher.beginDotComSignIn(cb)
} else {
  this.dispatcher.beginEnterpriseSignIn(cb)
  await this.dispatcher.setSignInEndpoint(origin)
}
``` [6](#0-5) 

Because `hostname !== 'github.com'`, this unconditionally invokes `beginEnterpriseSignIn`/`setSignInEndpoint(origin)` with `origin` pointed at the attacker's server, having skipped the real `isGitHubHost` verification that would normally gate this classification.

### Impact Explanation
This lets a malicious HTTPS git remote (something the attacker fully controls — the exact "remote response" scenario the scope calls out) impersonate a GitHub Enterprise host purely by crafting a response header, without needing the target to actually run GitHub Enterprise Server or pass any legitimacy check. The user is shown Desktop's normal-looking "Sign in" popup (`PopupType.SignIn`, `isCredentialHelperSignIn: true`) pointed at the attacker's endpoint. If the user proceeds with sign-in (PAT entry or OAuth) believing it is their legitimate enterprise instance, requests/credentials associated with that sign-in flow are directed to the attacker-controlled origin instead of a host the user actually authorized — i.e., a host-authorization bypass and a path toward credential/PAT exfiltration.

### Likelihood Explanation
Requires that: (1) the user attempts a git operation (clone/fetch/push) against an attacker-controlled HTTPS remote, and (2) does not already have a saved account for that endpoint, and (3) the user completes the resulting sign-in prompt. This is a realistic scenario for repository URLs pointing at attacker infrastructure (e.g., a malicious "enterprise" remote added via a cloned/malicious repo config or supply-chain link), and needs only a normal HTTP 401 response with a crafted header — no elevated capability is required.

### Recommendation
Do not use the client-controlled `WWW-Authenticate` header content as authoritative proof of a GitHub host. Either remove the `wwwauth[]` shortcut entirely and always fall back to `isGitHubHost(endpoint)`, or treat a `wwwauth[]` match only as a hint that triggers the same network-based `isGitHubHost` verification before classifying the endpoint as `enterprise`.

### Proof of Concept
1. Attacker hosts an HTTPS git server at `https://evil.example.com/repo.git` that is not a GitHub Enterprise instance.
2. Victim adds/clones this as a remote in GitHub Desktop and performs a fetch/push; Desktop has no existing account for `evil.example.com`.
3. On the initial unauthenticated request, the attacker's server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this to Desktop's credential helper as `wwwauth[0]=Basic realm="GitHub"`.
5. `getEndpointKind` matches `realm="GitHub"` and returns `'enterprise'` without ever calling `isGitHubHost` [3](#0-2) .
6. Desktop shows the GitHub Enterprise sign-in popup with `credentialHelperUrl`/sign-in endpoint set to `https://evil.example.com` [7](#0-6) .
7. If the victim completes sign-in (PAT or OAuth) believing this is their real enterprise host, their credential/token exchange occurs against the attacker's origin.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-151)
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L167-178)
```typescript
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
