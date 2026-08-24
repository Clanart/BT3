## Title
Credential-helper trusts unauthenticated `WWW-Authenticate` realm to classify hosts as GitHub Enterprise, triggering a GHE sign-in flow scoped to an attacker-controlled endpoint - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

## Summary
The LSP6 report's core defect is a component making a security-relevant trust decision (interface support) based on a self-declared, unverified signal instead of an authoritative check. The same pattern exists in GitHub Desktop's `git credential` trampoline: `getEndpointKind()` classifies an arbitrary remote host as a GitHub/GitHub Enterprise endpoint by trusting the `WWW-Authenticate` HTTP header returned by the remote server itself, with no cryptographic or out-of-band verification. Because that header comes straight from the server Git is talking to, an attacker who controls a git remote/proxy can spoof it to make Desktop treat their server as "enterprise", causing Desktop to automatically pop up a GitHub Enterprise sign-in flow pre-scoped to the attacker's origin.

## Finding Description
`getEndpointKind()` is used to decide whether the endpoint from a git credential request is `github.com`, `ghe.com`/`enterprise`, or `generic`: [1](#0-0) 

The relevant branch reads the `wwwauth[...]` entries that were captured from the remote server's HTTP response and forwarded verbatim into the credential map, and if the value contains `realm="GitHub"` it unconditionally returns `'enterprise'` — with no check that the host is actually `*.ghe.com`/allow-listed, no TLS certificate pinning, and no confirmatory request to the server (unlike the fallback `isGitHubHost()` path a few lines below it, which at least makes a HEAD request and inspects `x-github-request-id`).

This classification directly drives `getCredential()`: [2](#0-1) 

When `endpointKind !== 'generic'` and there is no existing account for that endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`, which for any non-`github.com` hostname invokes `beginEnterpriseSignIn` and pre-populates the sign-in endpoint with the attacker-controlled origin: [3](#0-2) 

The invariant that is broken is: "a remote server's own claim about being a GitHub host is sufficient to drive a security-relevant workflow (initiating an authenticated GHE sign-in against that same server)". Existing guards (`isDotCom`, `isGHE`, the account-origin match in `findGitHubTrampolineAccount`) only protect the token *storage/retrieval* path — they correctly avoid handing out already-stored tokens to non-matching origins (`findGitHubTrampolineAccount` performs a strict origin comparison, see `app/src/lib/trampoline/find-account.ts:26-28`). But they do nothing to stop the spoofed-header path from triggering a brand-new sign-in flow aimed at the attacker's server in the first place.

## Impact Explanation
A malicious or compromised git server/proxy (e.g., a self-hosted "enterprise" clone URL, or a MITM on an HTTP/insecure network path terminating in the app's git process) can respond to a credential request with `WWW-Authenticate: Basic realm="GitHub"`. This causes Desktop to spontaneously open a "Sign in to GitHub Enterprise" dialog whose target server field is filled with the attacker's origin instead of the user's real GHE instance. A user who is accustomed to Desktop asking them to sign in during clone/fetch/push operations may enter their real GHE personal access token or credentials into this dialog, which are then sent directly to the attacker's server — a credential/token exfiltration outcome that matches the report's "unprivileged...attacker controls a git remote/proxy response...credential/token exfiltration" impact class.

## Likelihood Explanation
The attacker only needs to control the HTTP response for a git-over-HTTPS/HTTP remote the victim adds or is directed to (no local access, no admin rights, no prior malware, and no leaked credentials required). Triggering the credential-helper path is a normal part of any `git fetch`/`clone`/`push` against a URL that returns a 401 with a crafted `WWW-Authenticate` header — this is a standard git server behavior, not an unnatural user action. The only user action required is following/adding a link to the attacker's remote and completing a sign-in prompt that Desktop itself surfaced, which is expected in-app behavior.

## Recommendation
Do not use the unauthenticated `WWW-Authenticate` realm string alone to elevate an unknown host into the `enterprise` credential-kind that triggers a sign-in workflow. At minimum:
- Require confirmation with the `isGitHubHost()` network probe (or equivalent verified check) before trusting a `realm="GitHub"` header, rather than treating it as a standalone happy path.
- When `promptForGitHubSignIn` is invoked as a side effect of a credential request from an unrecognized host, clearly surface to the user that the sign-in target is derived from the remote itself and was not previously configured, so they can recognize a spoofed prompt.

## Proof of Concept
1. Stand up an HTTPS git server (or the standard git http-backend) that responds to unauthenticated requests with `401` and header `WWW-Authenticate: Basic realm="GitHub"`.
2. In GitHub Desktop, clone/add this server's repository as a remote and perform a fetch/push that requires authentication.
3. Git invokes Desktop's askpass/credential trampoline, forwarding the spoofed `wwwauth[...]` header into `getCredential`/`getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:137-165`).
4. Because no account exists for this origin, Desktop calls `ui.promptForGitHubSignIn(endpoint)` (`app/src/lib/trampoline/trampoline-credential-helper.ts:118`), which opens a GitHub Enterprise sign-in dialog pre-targeted at the attacker's origin (`app/src/lib/trampoline/trampoline-ui-helper.ts:87-93`).
5. If the user completes sign-in (PAT or basic auth) believing this is their real GHE instance, the credential is transmitted to the attacker-controlled server.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-165)
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
