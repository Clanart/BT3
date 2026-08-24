### Title
Spoofed `WWW-Authenticate: realm="GitHub"` header from a malicious/compromised remote silently triggers the trusted "Sign in to GitHub Enterprise" flow for an attacker-chosen endpoint - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
GitHub Desktop's credential-helper trampoline decides whether an unknown Git remote "is a GitHub host" partly by trusting the `wwwauth[]` fields that Git forwards from the **remote server's own HTTP response** into the credential-helper protocol. If any of those header values contains `realm="GitHub"`, Desktop classifies the endpoint as `'enterprise'` and, if no existing account matches, automatically pops the trusted native "Sign in to GitHub Enterprise" dialog pre-filled with the attacker-controlled endpoint. This can be triggered simply by fetching/cloning from (or being MITM/redirected to) a remote that returns a crafted 401 response — no local access, malware, or unnatural user action beyond a normal `git fetch`/`clone` is required.

### Finding Description
`getEndpointKind` in [1](#0-0)  classifies a remote endpoint using, among other signals, header values supplied by Git itself:

```
for (const [k, v] of cred.entries()) {
    if (k.startsWith('wwwauth[')) {
      if (v.includes('realm="GitHub"')) {
        return 'enterprise'
      } ...
```

These `wwwauth[N]` entries originate from the actual HTTP `WWW-Authenticate` response header sent by whatever server Git is talking to — i.e., the remote is fully attacker-controlled content (a malicious "git server", a compromised/typosquatted host the user added as a remote, or a MITM/proxy response). There is no validation that the host is a genuine GitHub Enterprise instance (no TLS pinning, no round-trip probe beyond trusting the header, no allow-list) before this classification is used.

Once classified as `'enterprise'`, `getCredential` in the same file ( [2](#0-1) ) checks whether any known account already matches `apiEndpoint`; if not, it calls:

```
const account = await ui.promptForGitHubSignIn(endpoint)
```

`promptForGitHubSignIn` ( [3](#0-2) ) then automatically invokes `dispatcher.beginEnterpriseSignIn(cb)` and `setSignInEndpoint(origin)` using the attacker-supplied `endpoint`, and shows the standard `PopupType.SignIn` dialog — the same native, trusted UI a user would normally only reach by manually choosing "Enterprise sign in" and typing a URL they intend to trust. Here, the URL is instead silently populated by whatever remote the user (or a redirect/proxy) pointed Git at during a fetch/clone/push, with no explicit intent from the user to authenticate to that specific host.

This happens inside the trampoline credential-helper flow which is invoked for every git network operation via `withTrampolineEnv`/`GIT_CONFIG_PARAMETERS: 'credential.helper=desktop'` ( [4](#0-3) ), so it fires for ordinary `fetch`, `clone`, `push`, `pull` operations against any remote — including ones supplied by a cloned/fetched repository's configuration or a proxy in the path.

### Impact Explanation
This is an unprompted, UI-level trust escalation: an attacker who controls (or can intercept traffic to) a remote that the user's Desktop instance connects to can cause Desktop's own native "Sign in to GitHub Enterprise" dialog to appear, pre-populated with the attacker's endpoint. Because this dialog is indistinguishable from a legitimate Desktop-initiated Enterprise sign-in, a user who proceeds will initiate an OAuth/basic-auth flow against the attacker's host, and Desktop will then store whatever account/token comes back from that host as if it were a legitimate account (`AccountsStore.addAccount`, `TokenStore` in `app/src/lib/stores/token-store.ts`). This maps directly to "unauthorized OAuth or account binding" driven by a git remote/proxy response, satisfying the report's underlying theme (sensitive credential material handled with insufficient verification/scoping) translated into Desktop's actual credential trust boundary.

### Likelihood Explanation
Likelihood is moderate: it requires only that Git receive a 401/407-style response carrying a crafted `WWW-Authenticate` header from the configured remote (or from a MITM/corporate proxy, or a redirect target) during a routine `fetch`/`clone`/`push`. No social engineering beyond convincing/tricking the user into adding or using a malicious remote (or intercepting network traffic to an existing one) is needed, and no local/admin access or pre-existing malware is required. The main mitigating factor is that a further user click (through the sign-in popup) is needed before any real damage (entering credentials) occurs, but the popup itself is triggered with no consent.

### Recommendation
- Do not trust the `wwwauth[]` realm string alone to classify a host as GitHub/Enterprise; require corroborating evidence (e.g., a successful `isGitHubHost` capability probe, matching against a user-configured/allow-listed Enterprise endpoint, or requiring the user to have explicitly added that endpoint before).
- Never auto-populate or auto-trigger the "Sign in to GitHub Enterprise" flow from data the remote server controls; if server-driven realm hints are used at all, surface the endpoint clearly and require explicit user confirmation that they intend to add this specific host before showing the sign-in popup.
- Apply the same interactive/background-task gating used for generic credentials (`getIsBackgroundTaskEnvironment` check, present in `getGenericCredential`) to the GitHub sign-in prompt path so unattended git operations (e.g., LFS smudge filters, submodule fetches) can never surface this dialog at all.

### Proof of Concept
1. Attacker sets up (or MITMs/compromises) a Git-over-HTTPS server at `https://ghe-mirror.attacker.example`.
2. User adds this URL as a remote (e.g., believing it's a mirror) or Desktop is redirected there via a compromised proxy while doing a normal `git fetch`.
3. When Git requests credentials for that URL, the server responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
4. Git forwards this as `wwwauth[0]=Basic realm="GitHub"` to Desktop's credential helper trampoline.
5. `getEndpointKind` (`app/src/lib/trampoline/trampoline-credential-helper.ts:157-165`) returns `'enterprise'`.
6. Since no stored account matches `ghe-mirror.attacker.example`, `getCredential` calls `ui.promptForGitHubSignIn('https://ghe-mirror.attacker.example')`.
7. `promptForGitHubSignIn` (`app/src/lib/trampoline/trampoline-ui-helper.ts:80-104`) opens Desktop's native Enterprise sign-in flow pointed at the attacker's host without any prior user action indicating intent to trust it.

Note: I was not able to fully trace whether the OAuth flow initiated by `beginEnterpriseSignIn`/`setSignInEndpoint` performs any additional endpoint validation before opening the browser to the attacker's `login/oauth/authorize` URL (e.g., in `sign-in-store.ts` / `enterprise-validate-url.ts`); confirming the exact browser-facing behavior would require deeper tracing of `SignInStore.beginEnterpriseSignIn` and the enterprise URL validation logic, which is only partially covered by the indexed code shown here.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L137-166)
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

**File:** app/src/lib/trampoline/trampoline-environment.ts (L123-147)
```typescript
      return await fn({
        DESKTOP_PORT: await trampolineServer.getPort(),
        DESKTOP_TRAMPOLINE_TOKEN: token,
        GIT_ASKPASS: '',
        // This warrants some explanation. We're configuring the
        // credential helper using environment variables rather than
        // arguments (i.e. -c credential.helper=) because we want commands
        // invoked by filters (i.e. Git LFS) to be able to pick up our
        // configuration. Arguments passed to git commands are not passed
        // down to filters.
        //
        // We're using the undocumented GIT_CONFIG_PARAMETERS environment
        // variable over the documented GIT_CONFIG_{COUNT,KEY,VALUE} due
        // to an apparent bug either in a Windows Python runtime
        // dependency or in a Python project commonly used to manage hooks
        // which isn't able to handle the blank environment variables we
        // need when using GIT_CONFIG_*.
        //
        // See https://github.com/desktop/desktop/issues/18945
        // See https://github.com/git/git/blob/ed155187b429a/config.c#L664
        GIT_CONFIG_PARAMETERS: `${gitEnvConfigPrefix}'credential.helper=' 'credential.helper=desktop'`,

        GIT_USER_AGENT: await GitUserAgent(),
        ...sshEnv,
      })
```
