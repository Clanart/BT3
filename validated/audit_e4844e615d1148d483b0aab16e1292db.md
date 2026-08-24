## Title
Unauthenticated `WWW-Authenticate` header spoofing lets an attacker-controlled git host classify itself as "GitHub", triggering an unsolicited GitHub sign-in prompt bound to attacker infrastructure - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop's credential trampoline decides whether an unknown git remote host should be treated as a genuine GitHub/GitHub Enterprise host based on a `WWW-Authenticate` header value that the *remote server itself* returns during a Git HTTP authentication handshake. This value is fully attacker-controlled when the user performs a clone/fetch/push against a server the attacker operates (or a machine-in-the-middle proxy on an `http://` remote). If the header claims `realm="GitHub"`, Desktop short-circuits its real verification (`isGitHubHost`, an actual API probe) and classifies the endpoint as `'enterprise'`, which then causes Desktop to surface its own trusted "Sign in to GitHub" UI pointed at the attacker's host - all without the user ever intending to add a GitHub account.

### Finding Description
In `getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:137-179`), the classification of a credential-request host proceeds through a series of checks. Crucially, before the app performs its own network-based verification (`isGitHubHost(endpoint)` at line 178, which actually probes the host), it inspects Git's forwarded `wwwauth[]` credential fields: [1](#0-0) 

This comment itself acknowledges the header is used as a "happy-path" shortcut, but the header originates entirely from the remote server's HTTP response and is never cryptographically or otherwise validated - it's exactly analogous to the DeFi bug class where an untrusted, attacker-supplied input is treated as sufficient authorization to perform a privileged action (here: "trust this host as a GitHub identity provider").

This classification feeds directly into `getCredential` (`app/src/lib/trampoline/trampoline-credential-helper.ts:93-125`): [2](#0-1) 

If `endpointKind !== 'generic'` (i.e., the attacker's fake `realm="GitHub"` header made it look like an enterprise GitHub host) and no existing account already matches that endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` - unconditionally, as a background effect of the user simply cloning/fetching from that URL in Desktop, not as a result of any explicit "Add account" action by the user.

Separately, `getGitHubCredential` (lines 50-57) does correctly scope existing stored accounts by exact origin match via `findGitHubTrampolineAccount`, so already-authenticated credentials are not leaked to arbitrary hosts. The exploitable gap is specifically the *unauthenticated trust decision* that turns an arbitrary remote into something Desktop treats as "GitHub-like" purely from a self-reported header, before its own authoritative check (`isGitHubHost`) runs.

### Impact Explanation
An attacker who controls a git server (or a proxy/MITM position for an `http://` remote) can, without any prior trust or user action beyond adding/cloning that remote in Desktop, cause the application to present its native "Sign in to GitHub" dialog scoped to the attacker's own host. Because this dialog is rendered by Desktop's own trusted UI chrome, it can be used as a highly convincing phishing vector: victims who believe they are authenticating to a legitimate corporate GitHub Enterprise instance are instead initiating an OAuth/credential flow against attacker infrastructure, aiding credential/token exfiltration and unauthorized account binding within Desktop. This satisfies the "unrestricted approval"-class analog: a privileged classification (`'enterprise'`/GitHub-trusted) is granted to attacker-supplied data without proper authorization checks.

### Likelihood Explanation
Triggering the path only requires the victim to add a remote/clone from, or otherwise let Desktop perform a fetch/push authentication attempt against, a server the attacker controls - no local access, no prior compromise, and no unnatural steps beyond normal repository interaction. The `isGitHubHost` probe (the "real" check) only executes after the header-based shortcut has already been consulted and can already have caused sign-in prompting.

### Recommendation
Do not use the unauthenticated `wwwauth[]` `realm=` value as authoritative classification of a host as GitHub/Enterprise. Perform the `isGitHubHost` verification (or equivalent authenticated check against a known allowlist of account endpoints) before deciding whether to display any GitHub-branded sign-in prompt, and never trigger `promptForGitHubSignIn` for a host that has not been independently verified.

### Proof of Concept
1. Attacker stands up an HTTP git server (or a MITM proxy in front of an `http://` remote) that, on authentication challenge, returns a header such as `WWW-Authenticate: Basic realm="GitHub"`.
2. Victim adds this URL as a remote in GitHub Desktop and performs a fetch/push.
3. Git forwards the response's `WWW-Authenticate` value to Desktop's credential helper as a `wwwauth[]` field.
4. `getEndpointKind` (`trampoline-credential-helper.ts:157-165`) matches `realm="GitHub"` and returns `'enterprise'` before `isGitHubHost` is consulted.
5. `getCredential` (`trampoline-credential-helper.ts:109-125`) finds no existing account for this endpoint and calls `ui.promptForGitHubSignIn(endpoint)`, showing the victim a native "Sign in to GitHub" dialog bound to the attacker's host.

Note: I was unable to inspect the implementation of `promptForGitHubSignIn` / the downstream OAuth-authorize flow (outside the indexed portions of this codebase) to fully confirm the exact mechanics of what credentials would be transmitted to the attacker's endpoint versus opened in an external browser; a Devin session with full repository access would be needed to trace `trampoline-ui-helper.ts` and the sign-in-store OAuth flow end-to-end to fully characterize the downstream exfiltration impact.

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
