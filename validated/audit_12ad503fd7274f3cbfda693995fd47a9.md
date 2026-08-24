## Title
Attacker-controlled `WWW-Authenticate` header can force GitHub Desktop into a GitHub Enterprise OAuth sign-in flow against an arbitrary host - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

## Summary
The external report's broken invariant is "a value that governs security-sensitive downstream behavior is derived unsafely (never explicitly set / set from an untrusted source), so code that trusts it either fails-open or fails-closed incorrectly." The closest Desktop analog is `getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts`, which classifies a Git remote endpoint as `'enterprise'` based solely on an attacker-controllable HTTP response header (`WWW-Authenticate`), and that classification is then used to automatically kick off a GitHub Enterprise sign-in/OAuth flow against the attacker's own host.

## Finding Description
When Git needs credentials for an HTTPS remote, it invokes the `credential.helper` trampoline, which calls `getCredential` → `getEndpointKind`: [1](#0-0) 

Note the loop that inspects `wwwauth[...]` credential fields, which Git populates directly from the server's `WWW-Authenticate` response header:

```
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

This value is fully controlled by whatever server the user's remote points to - a malicious remote, fork, or MITM-capable proxy can simply respond with `WWW-Authenticate: Basic realm="GitHub"` to any HTTP request that requires auth (e.g. a private-looking repo, or an LFS/submodule endpoint), independent of the actual hostname.

That classification then drives `getCredential`: [2](#0-1) 

Because `endpointKind !== 'generic'` and no stored account matches this arbitrary host, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, where `endpoint` is the attacker-controlled URL: [3](#0-2) 

Since `hostname !== 'github.com'`, this branch calls `dispatcher.beginEnterpriseSignIn(cb)` followed by `dispatcher.setSignInEndpoint(origin)`, where `origin` is derived from the attacker's URL - i.e. Desktop programmatically starts its "GitHub Enterprise" sign-in machinery pointed at a host the attacker fully controls, and shows a `SignIn` popup flagged `isCredentialHelperSignIn: true` with `credentialHelperUrl` set to that same attacker URL.

The only gate preventing this from firing for background/non-interactive git operations is `getIsBackgroundTaskEnvironment`, and the only gate preventing it from matching an already-signed-in account is an exact `endpoint` string match - neither of which validates that the host claiming `realm="GitHub"` is a real GitHub/GHE instance. There is no allowlist, TLS-based verification, or DNS/known-endpoint check tying the `'enterprise'` classification to the platform's actual list of GitHub Enterprise hosts (`isGHE`/`supportsRepoRules`-style checks exist elsewhere but are bypassed by the `wwwauth` branch, which runs before any network-based `isGitHubHost` check and short-circuits with `return 'enterprise'`).

## Impact Explanation
This is exploitable purely by controlling the server behind a Git remote/fork that the user has already added (or that a submodule/LFS endpoint points to) - no local access, no prior malware, no leaked credentials required. The practical effect is that GitHub Desktop's own "GitHub Enterprise sign-in" UI (which users are trained to trust, since it's a first-party Desktop dialog) is silently launched against the attacker's chosen origin whenever the attacker's server issues a spoofed `WWW-Authenticate` challenge in response to a normal fetch/push/pull. This creates a credible phishing/credential-collection vector and an unauthorized "account binding": the sign-in flow associates whatever the user enters with the attacker's endpoint inside Desktop's own account store, and any OAuth/PAT flow driven from that dialog is directed at the attacker's server rather than the real GitHub/GHE instance. This matches the valid-impact categories of "unauthorized OAuth or account binding" and "credential/token exfiltration" triggered by "a git remote/proxy response."

## Likelihood Explanation
Likelihood is Low-to-Medium: it requires the victim to interact with a repository/remote/submodule/LFS endpoint controlled by the attacker (a realistic scenario for cloned/forked untrusted repos, similar to the report's own low-likelihood/high-impact framing), and it requires the credential helper `get` flow to actually be invoked (i.e., a Git operation that needs HTTP auth against that host). It does not require any social engineering beyond the user performing a normal clone/fetch/push against an attacker-supplied URL.

## Recommendation
- Do not classify an endpoint as `'enterprise'` based solely on the `wwwauth[...]` realm string; treat it only as a weak heuristic hint and always corroborate it with an actual network check (the existing `isGitHubHost(endpoint)` call, or a real GHE capability probe) before starting the Enterprise sign-in flow.
- Alternatively, remove the `wwwauth` fast-path entirely and rely solely on `isDotCom`/`isGHE`/`isGitHubHost`, which are enforced by the app rather than by data returned from the remote.
- Surface the actual target host prominently in the `SignIn` dialog when `isCredentialHelperSignIn` is true, so users have a chance to notice a mismatched domain even if the heuristic misfires.

## Proof of Concept
1. Attacker sets up an HTTPS server at `https://evil.example.com/foo.git` (or an LFS/submodule endpoint referenced by a repo the victim clones).
2. Victim adds this as a remote (or the repo already contains it as a submodule/LFS server) and performs any Git network operation requiring auth (`git fetch`, `git push`, LFS pull, etc.) from within GitHub Desktop.
3. The attacker's server responds to the credential-requiring request with `WWW-Authenticate: Basic realm="GitHub"`.
4. Git's credential helper protocol forwards this as a `wwwauth[0]=Basic realm="GitHub"` field to Desktop's `credential.helper` trampoline (`createCredentialHelperTrampolineHandler` → `getCredential` → `getEndpointKind`).
5. `getEndpointKind` returns `'enterprise'` purely from that header [4](#0-3) , and since no account exists for `evil.example.com`, `getCredential` calls `ui.promptForGitHubSignIn('https://evil.example.com/...')`.
6. `promptForGitHubSignIn` starts the Enterprise sign-in flow (`beginEnterpriseSignIn` + `setSignInEndpoint(origin)`) against `evil.example.com` and shows the sign-in popup [3](#0-2) , leading the user to authenticate against the attacker's server through Desktop's own trusted UI chrome.

Note on confidence: I was not able to fully trace `dispatcher.beginEnterpriseSignIn`/`setSignInEndpoint` all the way through `sign-in-store.ts` and `sign-in.tsx` in this session (index limits/remaining iterations), so I cannot confirm with certainty exactly what network calls or OAuth token exchange occur once the popup is shown, nor whether any additional validation exists deeper in that flow that would reduce the impact. A Devin session with full repository access would be needed to trace `SignInStore.authenticateWithBrowser`/`authenticateWithBasicAuth` and the `sign-in.tsx` component to confirm the exact downstream consequence (token exposure vs. just a misleading UI prompt).

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
