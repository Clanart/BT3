### Title
Spoofable `WWW-Authenticate` realm header lets any git host trigger Desktop's trusted "Sign in to GitHub Enterprise" flow against an attacker-controlled origin - (File: app/src/lib/trampoline/trampoline-credential-helper.ts)

### Summary
`getEndpointKind` decides whether a host git is authenticating against should be treated as a real GitHub Enterprise (`'enterprise'`) endpoint by inspecting `wwwauth[N]` credential-helper input fields, which are populated directly from the `WWW-Authenticate` response header returned by the remote git server. If any such header value contains `realm="GitHub"`, the endpoint is unconditionally classified as `'enterprise'`, bypassing the network-verified `isGitHubHost()` check entirely.

### Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts` [1](#0-0)  iterates the `wwwauth[N]` fields Git forwards to the credential helper and, if any value matches `realm="GitHub"`, immediately returns `'enterprise'` — without ever calling the network-based `isGitHubHost(endpoint)` verification that is used as the fallback for unrecognized hosts [2](#0-1) . These `wwwauth[N]` values originate from the `WWW-Authenticate` header of an HTTP 401 response sent by the remote git server/proxy, which is fully attacker-controlled when the user adds or interacts with a malicious/compromised git remote.

When `getCredential` calls `getEndpointKind` and it returns anything other than `'generic'`, and there is no existing stored account for that host, Desktop calls `ui.promptForGitHubSignIn(endpoint)` [3](#0-2) . For a non-`github.com` hostname, `promptForGitHubSignIn` calls `dispatcher.beginEnterpriseSignIn(cb)` and `dispatcher.setSignInEndpoint(origin)` using the attacker-supplied `origin`, then shows the official `SignIn` popup pre-configured with `credentialHelperUrl: endpoint` [4](#0-3) . This means Desktop's legitimate "Sign in to GitHub Enterprise" UI is opened and bound to whatever origin the attacker's server chooses, purely because that server echoed a specific string in a response header — something entirely within the attacker's control and requiring no actual GitHub-compatible server behavior.

### Impact Explanation
If the user, believing this is a legitimate authentication prompt from Desktop (triggered while trying to clone/fetch/push against a remote they added), proceeds with the sign-in flow (basic auth or PAT entry), their credentials/token are submitted against the attacker-controlled `origin`, resulting in credential/token exfiltration. This is achievable purely through a malicious git remote/proxy's HTTP response, which is an explicitly in-scope attacker capability. This bypasses the intended stronger verification (`isGitHubHost`) that would otherwise gate this trust decision for hosts without a recognized realm string.

### Likelihood Explanation
Exploitation requires the victim to add or interact with an attacker-controlled git remote (e.g., cloning a malicious repository over HTTP(S)) and to actually complete a sign-in prompt that appears unexpectedly. This is plausible in a social-engineering-adjacent context (a repo that appears legitimate but is hosted on attacker infrastructure demanding auth), but the practical exploitation still depends on the user manually entering real credentials into the resulting dialog, which somewhat reduces likelihood.

### Recommendation
Do not use the client-supplied `WWW-Authenticate` realm value as sufficient evidence to classify a host as a verified GitHub/GitHub Enterprise endpoint. Either remove the `wwwauth[N]` "happy path" short-circuit entirely and always fall back to the network-verified `isGitHubHost()` check, or require that the header-based classification be corroborated by the same verification used in the fallback path before triggering the enterprise sign-in UI. Additionally, clearly surface the target origin in the sign-in dialog so users are less likely to be misled about which host they are authenticating with.

### Proof of Concept
1. Host an HTTP git server (e.g., `https://evil.example.com/repo.git`) that responds to unauthenticated requests with `401` and a header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone/add this remote and attempt a fetch/push that triggers git's credential helper.
3. `getCredential` → `getEndpointKind` sees the spoofed header and returns `'enterprise'` [5](#0-4) , skipping the real `isGitHubHost` check.
4. Since no account exists for `evil.example.com`, Desktop calls `promptForGitHubSignIn('https://evil.example.com/...')`, which opens the trusted-looking "Sign in to GitHub Enterprise" popup targeting `evil.example.com` [6](#0-5) .
5. If the user completes sign-in (enters credentials/PAT), those are sent to the attacker-controlled origin.

Note: I was unable to inspect the implementation of `isGitHubHost` in `app/src/lib/api.ts` within this session (grep for its definition did not resolve), so I cannot fully confirm how much stricter that fallback check is compared to the header-based shortcut; this is worth verifying further, potentially via a full Devin session with file access, to confirm the exact trust delta this shortcut introduces.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L101-125)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L172-178)
```typescript
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
