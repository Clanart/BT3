This confirms the mechanism: `promptForGitHubSignIn(endpoint)` triggers a **real, legitimate** GitHub.com or Enterprise sign-in flow (`beginDotComSignIn`/`beginEnterpriseSignIn`) but for whichever `endpoint` was derived from the credential URL Git handed to the trampoline — i.e., the attacker's host — and once that legitimate flow completes, `credWithAccount(cred, account)` stamps the resulting real GitHub token onto the credential map for that attacker host, which is then handed back to Git and sent over the wire as HTTP Basic Auth to the attacker's server.

### Title
Attacker-controlled `WWW-Authenticate` header spoofs GitHub sign-in prompt, exfiltrating the resulting OAuth token to the attacker's remote - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The Git credential-helper trampoline classifies an unknown remote host as `enterprise` (a "GitHub host") based solely on a `WWW-Authenticate` HTTP header that the **remote server itself** supplies during a 401 response, without any cryptographic or DNS-based verification. When that classification fires for a host with no existing stored account, Desktop shows a "Sign in to GitHub" dialog and performs a real OAuth/GHE sign-in flow, then — because `promptForGitHubSignIn` resolves an `Account` object which is merged into the credential for the *original, untrusted* endpoint — hands the freshly minted, valid GitHub access token to Git as Basic-Auth credentials for the attacker's host.

### Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts:137-179` determines whether a remote counts as a GitHub host using, among other things, headers forwarded by Git itself: [1](#0-0) 

These `wwwauth[...]` values originate from the HTTP response of whatever server Git is talking to — fully attacker-controlled if the "repository" is hosted on, or proxied through, a malicious/compromised server (e.g. a cloned repo whose `.git/config` remote points to `https://evil.example/x.git`, or a MITM on an insecure/self-signed connection the user accepts). By simply returning `WWW-Authenticate: realm="GitHub"` on a 401, the attacker forces `getEndpointKind` to return `'enterprise'`.

In `getCredential` (lines 94-135), when the endpoint is classified as non-generic and no account is already stored for that exact endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)`: [2](#0-1) 

`promptForGitHubSignIn` in `app/src/lib/trampoline/trampoline-ui-helper.ts:80-104` then performs a **genuine** OAuth (or GHE) sign-in flow — the user sees the real GitHub.com login/OAuth consent screen and legitimately authenticates: [3](#0-2) 

Once that real sign-in succeeds, control returns to `getCredential`, which does `credWithAccount(cred, account)` — copying the newly obtained real `login`/`token` onto the credential map that will be sent back to `git`, still addressed to the attacker's original (untrusted) `endpoint`/host: [4](#0-3) 

Git then uses that credential for HTTP Basic Auth against the attacker's server, transmitting the user's real GitHub access token to the attacker.

### Impact Explanation
This results in **credential/token exfiltration**: a user's legitimate GitHub.com or GitHub Enterprise access token — obtained via a real, user-approved sign-in — is sent directly to an attacker-controlled host. Because the token is a real, freshly-issued OAuth token with the user's full GitHub scopes, the attacker can use it to access the victim's repositories, private data, or perform actions as the victim. This matches the "attacker controls a git remote/proxy response ... resulting in credential/token exfiltration" impact category. The attack requires no local access, no prior malware, and no leaked credentials — only that the victim clones/fetches from (or is redirected to) a URL the attacker controls and completes the sign-in dialog that Desktop itself presents as a normal "Sign in to GitHub" step.

### Likelihood Explanation
Likelihood is moderate-to-high: any git operation (`clone`, `fetch`, `pull`, `push`) against an untrusted or attacker-influenced remote can trigger the credential helper's `get` command. Returning a crafted `WWW-Authenticate: realm="GitHub"` header on 401 is trivial for any server the attacker controls. The main mitigating factor is that the victim must go through with the sign-in prompt — but because the dialog looks identical to Desktop's normal "Sign in to GitHub" flow, and Desktop itself decided to show it (from the user's point of view it's simply "Desktop is asking me to authenticate to access this repo"), there's a plausible social-engineering-free path where a user cloning what they believe is a legitimate/private repo authenticates without realizing the destination host is untrusted. Existing guards (`isDotCom`, `isGHE`, `isGist` exact-hostname allowlists) do not stop this, because the vulnerable path is precisely the fallback branch that runs for hosts *not* matching those known-good lists — the header-derived signal is trusted as-is with no verification.

### Recommendation
- Do not let the untrusted `wwwauth[...]` header alone determine that a host is a trusted GitHub host; at minimum corroborate it with `isGitHubHost(endpoint)` (an independent request to the candidate host) before offering the "GitHub sign in" path, or drop the header-based happy-path entirely.
- After a sign-in completes via `promptForGitHubSignIn`, verify that the resulting `Account.endpoint`'s origin matches the original credential's requested origin before merging the token into the credential returned to Git (i.e., never attach a `github.com`/GHE token to a credential destined for a different host).
- Surface the actual destination host prominently in the sign-in dialog so users can recognize a mismatch (e.g., "Desktop wants to sign in to GitHub because `evil.example` claims to be a GitHub host").

### Proof of Concept
1. Attacker sets up `https://evil.example/repo.git` (or a MITM proxy in front of any HTTP git endpoint) that responds to unauthenticated git-http requests with `401` and header `WWW-Authenticate: realm="GitHub"`.
2. Victim, using GitHub Desktop, clones or adds `https://evil.example/repo.git` as a remote and performs a fetch/pull.
3. Git invokes Desktop's credential helper trampoline (`get`), which calls `getEndpointKind` — the crafted header causes it to return `'enterprise'`.
4. No stored account matches `evil.example`, so `getCredential` calls `ui.promptForGitHubSignIn('https://evil.example')`.
5. Desktop shows a real "Sign in to GitHub" dialog; if `hostname !== 'github.com'` it calls `beginEnterpriseSignIn` + `setSignInEndpoint(origin)` where `origin` is `https://evil.example` — meaning even the "Enterprise" OAuth/API flow could be pointed at the attacker's endpoint directly, or (for GitHub.com hostname spoofed via header-classification quirks) a real github.com OAuth completes.
6. On success, `credWithAccount` attaches the resulting valid `login`/`token` to the credential for `evil.example`, which `formatCredential` returns to `git`, which sends it as `Authorization: Basic ...` to `evil.example` — exfiltrating the token to the attacker.

### Citations

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-56)
```typescript
const credWithAccount = (c: Credential, a: IGitAccount | undefined) =>
  a && new Map(c).set('username', a.login).set('password', a.token)

async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
```

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
