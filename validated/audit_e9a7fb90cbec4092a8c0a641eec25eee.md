Based on the investigation, I found a credible analog centered on GitHub Desktop's credential-helper trampoline, where a git remote (attacker-controlled server or MITM proxy) can spoof its identity as a "GitHub" host purely through a forgeable HTTP header, causing Desktop to open its native GitHub sign-in/OAuth flow against an arbitrary attacker-chosen origin without any independent verification that the host is a real GitHub instance.

### Title
Git credential-helper trusts a forgeable `WWW-Authenticate` header to classify any remote as a GitHub host, triggering GitHub sign-in against an attacker-controlled origin - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The seed report is about a critical function trusting the wrong caller (`onlyStrategist` instead of `onlyGovernance`) — i.e., a security-relevant decision keyed off an insufficiently-privileged/insufficiently-verified signal. The Desktop analog is `getEndpointKind()` in the credential-helper trampoline, which decides whether a git remote should be treated as a GitHub/Enterprise host based on a header value supplied by the remote server itself, rather than an independently verified signal, and then uses that classification to launch a trust-conferring UI flow (GitHub sign-in).

### Finding Description
When Git needs credentials for an HTTPS remote, it invokes Desktop's registered `credential.helper=desktop`, which is implemented by `createCredentialHelperTrampolineHandler` [1](#0-0) . The `getCredential()` path calls `getEndpointKind()` to decide how to treat the remote [2](#0-1) .

`getEndpointKind()` inspects the `wwwauth[]` credential fields — which are populated by Git from the actual HTTP `WWW-Authenticate` response header sent by the remote server the user is cloning/fetching/pushing to — and if the header contains `realm="GitHub"`, the host is immediately classified as `'enterprise'`, before any real connectivity/identity check is performed: [3](#0-2) 

Only if that forged-header path doesn't match does the code fall back to an actual network probe via `isGitHubHost(endpoint)` [4](#0-3) . In other words, the "cheap" classification signal (an attacker-controlled response header) is checked and trusted *before* the more rigorous verification, and short-circuits it.

Once classified as non-`generic` and no existing account matches the derived API endpoint, Desktop calls `ui.promptForGitHubSignIn(endpoint)` [5](#0-4) . That function directs the Enterprise sign-in flow straight at the attacker-supplied origin without funneling through the manual/validated entry path: [6](#0-5) 

By contrast, when a user manually initiates an Enterprise sign-in from the UI, `SignInStore.setEndpoint()` performs URL/protocol validation before allowing progression to the Authentication step [7](#0-6) . The credential-helper-triggered path calls `dispatcher.setSignInEndpoint(origin)` directly instead of that validated `setEndpoint()` pipeline — I was not able to fully confirm, within the remaining tool budget, whether `setSignInEndpoint` performs equivalent validation before wiring the endpoint into the sign-in state; this is the one open question a reviewer should verify in `app-store.ts`/`dispatcher.ts`.

### Impact Explanation
An attacker who controls a git server, or who can man-in-the-middle a plain HTTP or misconfigured HTTPS remote, does not need to compromise anything beyond the ability to return a crafted `WWW-Authenticate: Basic realm="GitHub"` response to Git's authentication probe. This single forgeable header is sufficient to make Desktop treat the attacker's arbitrary host as a trusted GitHub Enterprise endpoint and surface Desktop's native, familiar "Sign in to GitHub Enterprise" dialog (`PopupType.SignIn`, `isCredentialHelperSignIn: true`) pointed at that host. If a user completes this native-looking flow, this can lead to OAuth/credential exchange with an attacker origin the user never explicitly typed or vetted, which lines up with the "unauthorized OAuth or account binding" impact category.

### Likelihood Explanation
This is triggered passively whenever Desktop performs a Git HTTPS operation (clone/fetch/push) against a repository whose remote returns the crafted header and for which the user has no matching stored account — i.e., simply adding/cloning an attacker-hosted or MITM'd repository is enough to reach the vulnerable classification code. It requires no local access, no prior credential leak, and no unusual interaction beyond the ordinary use of Desktop with a repository the user has already chosen to add.

### Recommendation
Do not let a value supplied by the remote server itself (`wwwauth[]`) short-circuit or precede independent host verification. Reorder `getEndpointKind()` so the network-based `isGitHubHost()` check (or equivalent certificate/identity pinning against known GitHub/Enterprise endpoints) is authoritative, and use the `wwwauth` heuristic only as a secondary hint, never as a standalone basis for invoking `promptForGitHubSignIn`. Additionally, ensure the credential-helper-driven sign-in path routes through the same validated `setEndpoint()`/`validateURL()` logic used for manual Enterprise sign-in before displaying any sign-in UI tied to a remote-supplied origin.

### Proof of Concept
1. Stand up a git-over-HTTPS server (or MITM proxy) at `https://attacker.example`.
2. Configure it so that on an authenticated `git-upload-pack`/`git-receive-pack` request it responds `401` with header `WWW-Authenticate: Basic realm="GitHub"`.
3. In Desktop, add/clone a repository pointing at `https://attacker.example/whatever.git` (the user has no existing account for this host).
4. Observe: `getEndpointKind()` returns `'enterprise'` purely from the forged header [8](#0-7) , bypassing the `isGitHubHost()` network check, and Desktop opens the GitHub Enterprise sign-in dialog pointed at `attacker.example` [9](#0-8) .

Note on confidence: the core forged-header classification bypass is directly confirmed in code. The full end-to-end severity depends on whether `dispatcher.setSignInEndpoint()` re-applies the same validation as `SignInStore.setEndpoint()`; I was unable to inspect that implementation before running out of tool calls, so this should be verified before treating the OAuth-exposure impact as fully confirmed.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L152-166)
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L220-248)
```typescript
export const createCredentialHelperTrampolineHandler: (
  store: AccountsStore
) => TrampolineCommandHandler = (store: Store) => async command => {
  const firstParameter = command.parameters.at(0)
  if (!firstParameter) {
    return undefined
  }

  const { trampolineToken: token } = command
  const input = parseCredential(command.stdin)

  if (__DEV__) {
    debug(
      `${firstParameter}\n${command.stdin
        .replaceAll(/^password=.*$/gm, 'password=***')
        .replaceAll(/^(.*)$/gm, '  $1')
        .trimEnd()}`
    )
  }

  try {
    if (firstParameter === 'get') {
      const cred = await getCredential(input, store, token)
      if (!cred) {
        const endpoint = `${getCredentialUrl(input)}`
        info(`could not find credential for ${endpoint}`)
        setHasRejectedCredentialsForEndpoint(token, endpoint)
      }
      return cred ? formatCredential(cred) : undefined
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

**File:** app/src/lib/stores/sign-in-store.ts (L394-459)
```typescript
  public async setEndpoint(url: string): Promise<void> {
    const currentState = this.state

    if (
      currentState?.kind !== SignInStep.EndpointEntry &&
      currentState?.kind !== SignInStep.ExistingAccountWarning
    ) {
      const stepText = currentState ? currentState.kind : 'null'
      return fatalError(
        `Sign in step '${stepText}' not compatible with endpoint entry`
      )
    }

    /**
     * If the user enters a github.com url in the GitHub Enterprise sign-in
     * flow we'll redirect them to the GitHub.com sign-in flow.
     */
    if (/^(?:https:\/\/)?(?:api\.)?github\.com($|\/)/.test(url)) {
      this.beginDotComSignIn(currentState.resultCallback)
      return
    }

    this.setState({ ...currentState, loading: true })

    let validUrl: string
    try {
      validUrl = validateURL(url)
    } catch (e) {
      let error = e
      if (e.name === InvalidURLErrorName) {
        error = new Error(
          `The GitHub Enterprise instance address doesn't appear to be a valid URL. We're expecting something like https://example.ghe.com.`
        )
      } else if (e.name === InvalidProtocolErrorName) {
        error = new Error(
          'Unsupported protocol. Only https is supported when authenticating with GitHub Enterprise instances.'
        )
      }

      this.setState({ ...currentState, loading: false, error })
      return
    }

    const endpoint = getEnterpriseAPIURL(validUrl)

    const existingAccount = this.accounts.find(x => x.endpoint === endpoint)

    if (existingAccount) {
      this.setState({
        kind: SignInStep.ExistingAccountWarning,
        endpoint,
        existingAccount,
        error: null,
        loading: false,
        resultCallback: currentState.resultCallback,
      })
    } else {
      this.setState({
        kind: SignInStep.Authentication,
        endpoint,
        error: null,
        loading: false,
        resultCallback: currentState.resultCallback,
      })
    }
  }
```
