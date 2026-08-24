### Title
Attacker-controlled `WWW-Authenticate` realm / hostname heuristic causes Desktop's credential helper to treat any HTTPS git host as a trusted "GitHub Enterprise" endpoint, triggering a native GitHub sign-in prompt scoped to the attacker's server - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
The credential-helper trampoline decides whether a remote git host is a genuine GitHub/GitHub Enterprise endpoint using heuristics that are fully controllable by the remote server itself (an attacker-run git host), not by any cryptographic or authoritative check. If the heuristic says "yes, this is GitHub," Desktop pops up its native "Sign in to GitHub Enterprise" dialog pointed at the attacker's origin.

### Finding Description
`getEndpointKind` in `app/src/lib/trampoline/trampoline-credential-helper.ts` classifies a remote endpoint before deciding how to source credentials for it: [1](#0-0) 

Two of its branches are attacker-controllable:

1. It trusts the `WWW-Authenticate` header Git forwards from the remote server. If that header contains `realm="GitHub"`, the endpoint is unconditionally classified as `'enterprise'` — no signature, no cert pinning, nothing but a string an attacker's own HTTP server chooses to send: [2](#0-1) 

2. Failing that, it falls back to `isGitHubHost`, whose hostname heuristic `/(^|\.)(github)\./.test(hostname)` classifies any domain containing a `github.` label (e.g. `github.attacker.com`, `internal.github.corp-mirror.com`) as GitHub, again with no verification beyond string matching: [3](#0-2) 

When `getCredential` sees a non-`generic` classification and no existing local account matches that exact endpoint, it calls `ui.promptForGitHubSignIn(endpoint)`: [4](#0-3) 

`promptForGitHubSignIn` then drives the real `SignIn` flow, scoped to whatever `endpoint` string was derived from the attacker's remote URL — for non-`github.com` hostnames it calls `dispatcher.beginEnterpriseSignIn` followed by `dispatcher.setSignInEndpoint(origin)`, using the attacker's own origin as the enterprise server address: [5](#0-4) 

The broken invariant is: **"credentialUrl's host is a legitimate GitHub Enterprise instance"** is inferred purely from attacker-suppliable material (an HTTP response header, or a hostname substring), yet that inference is used to open a native, first-party-branded GitHub sign-in dialog and to bind any resulting account/token to that attacker-chosen `endpoint`. This is structurally the same failure class as the reported "hard-coded/assumed peg" bug: a value that is presented to security logic as trustworthy/fixed (USDC = $1, sUSD = $1) is actually attacker-influenceable in practice, and downstream logic (liquidation vs. Desktop's credential trust decision) makes high-stakes decisions on that false assumption.

### Impact Explanation
A user who adds/clones from, or is redirected to, an attacker-controlled HTTPS git remote (e.g. `https://github.attacker.tld/foo/bar.git`, or any domain the attacker fully controls that either serves a crafted `WWW-Authenticate: realm="GitHub"` header on a 401, or matches the naive `github.` hostname regex) can cause Desktop to:
- Present its own native "Sign in to GitHub/GitHub Enterprise" UI scoped to the attacker's server (`dispatcher.setSignInEndpoint(origin)`), lending the attacker's phishing target Desktop's built-in credibility.
- Should the victim complete sign-in believing this is a legitimate internal GHE instance, any token/PAT entered is delivered to and validated against the attacker's endpoint — i.e., credential exfiltration, since the entire OAuth/basic-auth exchange for that "Enterprise" step targets the attacker-chosen origin, not any hardcoded, verified GitHub property.
- This directly matches the requested impact categories: "credential/token exfiltration" and "unauthorized OAuth or account binding," since a resulting `Account` gets persisted with `endpoint` equal to the attacker's origin and will be reused for future authenticated requests to that same attacker host.

No local/physical access, no admin rights, and no pre-existing malware are needed — only that the victim interacts with a remote the attacker controls (clone URL, added remote, or fetch), which is within the stated valid-impact scope.

### Likelihood Explanation
Moderate. The attacker needs the victim to add/clone a remote pointing at attacker infrastructure (a normal, expected Desktop workflow — cloning a repo link, adding a remote from a README, following a "contribute" link) and to trigger any authenticated git operation (fetch/push/clone of a private-looking repo) so that Git surfaces credential prompts, or to control an HTTPS host that returns a crafted 401 with `WWW-Authenticate: realm="GitHub"`, both trivially controllable by anyone who owns/administers the receiving server. No existing guard checks TLS certificate chains against GitHub's, verifies the `/meta` GitHub API signature independent of the attacker's own responses (that check is only reached as a last resort when the header/hostname heuristics don't already short-circuit to `'enterprise'`), or requires user-typed enterprise URLs (`enterprise-validate-url.ts`) that would at least force deliberate action — the header/hostname path bypasses `validateURL` entirely because it's driven by the git remote, not by manual entry in the Enterprise sign-in form.

### Recommendation
- Do not trust the `WWW-Authenticate` realm string alone to classify a host as GitHub; treat it only as a hint that further verification (e.g., the existing `/meta` probe with `x-github-request-id` check) must confirm, rather than as a substitute for it.
- Remove or substantially tighten the `/(^|\.)(github)\./` hostname regex fallback in `isGitHubHost` (`app/src/lib/api.ts`); an attacker can trivially register a domain containing the `github.` label.
- Before opening the Enterprise sign-in UI in response to a credential-helper request (`ui.promptForGitHubSignIn`), surface the actual destination host clearly and/or require the same explicit user confirmation/URL validation path used for manually-added Enterprise accounts (`validateURL`), so a git-remote-driven trust decision cannot silently reuse the "trusted" native sign-in UI for an unverified host.

### Proof of Concept
1. Attacker stands up an HTTPS git server at `https://github.attacker-mirror.tld/victim/repo.git` (satisfies the `/(^|\.)(github)\./` hostname regex), or any HTTPS host that responds to Git's credential probe with `WWW-Authenticate: realm="GitHub"`.
2. Victim, in GitHub Desktop, clones or adds this remote and performs a fetch/push that requires authentication.
3. Git's credential handshake reaches Desktop's trampoline; `getEndpointKind` (`trampoline-credential-helper.ts:137-179`) classifies the endpoint as `'enterprise'` purely from the header/hostname heuristic.
4. `getCredential` finds no existing account for this endpoint and calls `ui.promptForGitHubSignIn(endpoint)` (`trampoline-credential-helper.ts:118`), which calls `dispatcher.beginEnterpriseSignIn` + `dispatcher.setSignInEndpoint(origin)` with `origin = https://github.attacker-mirror.tld` (`trampoline-ui-helper.ts:88-93`).
5. Desktop shows its native GitHub Enterprise sign-in dialog scoped to the attacker's origin; any credentials/PAT the victim submits are sent to the attacker's server, and any resulting `Account` is bound to the attacker's endpoint for future use.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L153-179)
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

**File:** app/src/lib/api.ts (L2451-2454)
```typescript
  // github.example.com,
  if (/(^|\.)(github)\./.test(hostname)) {
    return true
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
