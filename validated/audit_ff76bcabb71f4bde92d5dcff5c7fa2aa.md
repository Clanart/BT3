## Finding

### Title
Spoofable `WWW-Authenticate` realm header lets an attacker-controlled git remote force GitHub Desktop's credential trampoline to treat it as a trusted GitHub Enterprise host, triggering an OAuth sign-in flow for an arbitrary attacker endpoint - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The external report's broken invariant is: a role that is supposed to be governed by a narrower authority (`ZEEVE_SUPER_ADMIN_ROLE`) is instead implicitly reachable/controllable through a default, over-broad authority (`DEFAULT_ADMIN_ROLE`) because the code never sets an explicit boundary (`_setRoleAdmin`) and instead relies on an assumption that turns out to be attacker-influenceable. The Desktop analog is in `getEndpointKind()` in `app/src/lib/trampoline/trampoline-credential-helper.ts`, where the classification of a git remote as a first-party "GitHub"/"enterprise" endpoint (a trust boundary that gates whether Desktop automatically shows a "Sign in to GitHub" prompt) is derived from an attacker-controlled value - the `WWW-Authenticate` header realm string returned by the remote git server itself - rather than from a validated, user-entered endpoint.

### Finding Description
When git performs an HTTPS operation (`clone`, `fetch`, `push`) against a remote that requires authentication, the remote server can respond with a `WWW-Authenticate` header. Git forwards this to Desktop's credential helper trampoline as `wwwauth[...]` parameters. `getEndpointKind()` inspects these attacker-supplied values: [1](#0-0) 

If the value contains `realm="GitHub"`, the endpoint is classified as `'enterprise'` — i.e., trusted as a first-party GitHub Enterprise host — purely on the basis of a string the remote server chose to send, with no TLS/host validation, no call to `isGitHubHost()`'s heuristics, and no reuse of the `validateURL()` checks that gate the normal, user-driven "sign in to GitHub Enterprise" flow (`app/src/ui/lib/enterprise-validate-url.ts`, used from `SignInStore.setEndpoint`).

That classification is then used in `getCredential()`: [2](#0-1) 

Because `endpointKind !== 'generic'` and no existing account matches the (attacker's) endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` set to the attacker's own remote URL — a value that never went through the manual enterprise-URL entry/validation step a user would normally have to complete. This directly parallels the smart-contract bug: a supposedly narrow/normal trust classification (`generic` git host) is silently escalated to a privileged one (`enterprise`/GitHub-trusted) by an authority (the remote server / attacker) that should not have control over that boundary, exactly as `DEFAULT_ADMIN_ROLE` implicitly controlled roles it wasn't meant to administer because no explicit `_setRoleAdmin` boundary was set.

### Impact Explanation
An attacker who controls a git remote (e.g., a malicious HTTPS git server, or a compromised/MITM-ed proxy in front of one) that the victim clones or fetches from can:
1. Force GitHub Desktop to pop the native "Sign in to GitHub" dialog scoped to the attacker's own domain, without the user ever having typed or validated that Enterprise URL themselves.
2. Cause the subsequent OAuth authorization flow (`getOAuthAuthorizationURL` / `authenticateWithBrowser`) to open the attacker's site in the system browser, since the `endpoint` used to build that URL is the attacker-chosen host, not a vetted GHE instance.

This matches the "unprivileged, attacker-controls-a-git-remote-response resulting in unauthorized OAuth/account binding" category: the attacker manufactures a spoofed trust signal that routes the victim into an authentication flow bound to infrastructure the attacker controls, under the guise of GitHub Enterprise sign-in, entirely from a passive git response header — no local access, no prior credential leak, and no unnatural extra steps beyond the ordinary "clone this repo" action.

### Likelihood Explanation
The trigger requires only that the victim perform a normal git operation (clone/fetch/push) against a repository/remote hosted or proxied by the attacker over HTTPS — squarely within the "attacker controls a git remote/proxy response" and "attacker controls a fetched repository" scope. Setting a custom `WWW-Authenticate` header is trivial for any server operator. The main mitigating factor, which I was not able to fully verify in the available code, is the exact end-user-visible behavior of `ui.promptForGitHubSignIn()` and whether it independently re-validates the endpoint or shows enough context to let a careful user distinguish it from a legitimate first-party prompt — I could not open `trampoline-ui-helper.ts` in this session to confirm.

### Recommendation
- Do not classify an arbitrary remote as `'enterprise'`/GitHub-trusted purely based on a client-supplied/remote-supplied `WWW-Authenticate` realm string.
- Route the header-based heuristic decision through the same `isGitHubHost()` verification the code already performs for other borderline hosts, or require it before ever calling `ui.promptForGitHubSignIn()`.
- Ensure any endpoint used to launch an OAuth flow is either a previously validated (`validateURL`) enterprise endpoint or `github.com`, never a bare value taken from a remote's response headers.

### Proof of Concept
1. Stand up an HTTPS git server (e.g., via `git http-backend`) that, for any authenticated request, responds with `WWW-Authenticate: Basic realm="GitHub"`.
2. Have the victim add this server as a remote in GitHub Desktop and perform a `fetch`/`clone`/`push`.
3. Git invokes the credential trampoline; `getEndpointKind()` reads the `wwwauth[...]` credential parameter containing the spoofed realm and returns `'enterprise'`. [3](#0-2) 
4. `getCredential()` finds no matching stored account for the attacker's endpoint and calls `ui.promptForGitHubSignIn(endpoint)` with `endpoint` equal to the attacker's own domain. [4](#0-3) 
5. If the user proceeds, the OAuth browser flow is initiated against the attacker's domain (`getOAuthAuthorizationURL`/`authenticateWithBrowser` in `app/src/lib/stores/sign-in-store.ts`), a flow the user never manually configured or validated.

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
