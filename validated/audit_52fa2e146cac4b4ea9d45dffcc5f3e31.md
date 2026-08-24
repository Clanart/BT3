No vulnerability found for this question.

**Analysis:**

`setHasRejectedCredentialsForEndpoint` in [1](#0-0)  does not release or return any credential. It only records, in an in-memory `Map<string, Set<string>>` keyed by the per-operation `trampolineToken`, that credentials were rejected for a given endpoint string. This state is later read by `getHasRejectedCredentialsForEndpoint`, which is used purely to decide whether to synthesize a friendlier "user cancelled authentication" error message when Git reports `terminal prompts disabled` [2](#0-1) . There is no code path where this function causes a credential/token/passphrase to be sent anywhere.

The actual credential-release sink is `getCredential`/`getGitHubCredential`/`getGenericCredential` in [3](#0-2) , which is invoked via the git credential-helper protocol (`get`/`store`/`erase`). The `endpoint`/`cred` values used there (`protocol`, `host`, `path`, `url`) are populated by Git itself, from the real connection it is making, via `GIT_CONFIG_PARAMETERS='credential.helper=desktop'` [4](#0-3) , not from arbitrary attacker-injected prompt text that Desktop parses out of untrusted content. Git's own credential-helper protocol is what determines the `host`/`protocol` fields sent to the helper based on the URL it is actually connecting to (including after redirects, which Git itself restricts for credential-carrying redirects).

Account lookup for a resolved credential request is done in `findGitHubTrampolineAccount` in [5](#0-4) , which performs a strict `origin` (`protocol + host + port`) equality check between the stored account's endpoint and the parsed URL of the credential request — not a substring/prefix match — so a similarly-named or subdomain host cannot match an unrelated stored account. Similarly, `findGenericTrampolineAccount` derives its lookup key via `urlWithoutCredentials` on the same Git-provided URL [6](#0-5) .

The only place where server-controlled content (a `WWW-Authenticate` header) influences behavior is in `getEndpointKind`, where a `realm="GitHub"` header can cause Desktop to classify the host as `'enterprise'` [7](#0-6) . However, this classification only affects whether Desktop prompts the user to sign in for a *new* GitHub-style account for that specific host/origin; it does not cause any *existing* stored credential for a different host to be released, because `getGitHubCredential`'s `findGitHubTrampolineAccount` still requires exact `origin` equality regardless of `endpointKind`.

I was unable to find any code path in the reachable files where `setHasRejectedCredentialsForEndpoint` or the credential-helper flow it belongs to would send a stored token/passphrase to a host other than the one Git itself is connecting to. The claimed invariant violation (credential released to wrong host driven by attacker-supplied prompt/URL text) is not present in this code.

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L16-26)
```typescript
export const setHasRejectedCredentialsForEndpoint = (
  trampolineToken: string,
  endpoint: string
) => {
  const set = hasRejectedCredentialsForEndpoint.get(trampolineToken)
  if (set) {
    set.add(endpoint)
  } else {
    hasRejectedCredentialsForEndpoint.set(trampolineToken, new Set([endpoint]))
  }
}
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L143-143)
```typescript
        GIT_CONFIG_PARAMETERS: `${gitEnvConfigPrefix}'credential.helper=' 'credential.helper=desktop'`,
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L174-192)
```typescript
      if (
        hasRejectedCredentialsForEndpoint.has(token) &&
        e instanceof GitError &&
        fatalPromptsDisabledRe.test(e.message)
      ) {
        const msg = 'Authentication failed: user cancelled authentication'
        const gitErrorDescription =
          getDescriptionForError(DugiteError.HTTPSAuthenticationFailed, '') ??
          msg

        const fakeAuthError = new GitError(
          { ...e.result, gitErrorDescription },
          e.args,
          msg
        )

        fakeAuthError.cause = e
        throw fakeAuthError
      }
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L50-135)
```typescript
async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
}

async function promptForCredential(cred: Credential, endpoint: string) {
  const parsedUrl = new URL(endpoint)
  const username = parsedUrl.username === '' ? undefined : parsedUrl.username
  const account = await ui.promptForGenericGitAuthentication(endpoint, username)
  info(`prompt for ${endpoint}: ${account ? 'completed' : 'cancelled'}`)
  return credWithAccount(cred, account)
}

async function getGenericCredential(cred: Credential, token: string) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGenericTrampolineAccount(token, endpoint)

  if (account) {
    info(`found generic credential for ${endpoint}`)
    return credWithAccount(cred, account)
  }

  if (getIsBackgroundTaskEnvironment(token)) {
    debug('background task environment, skipping prompt')
    return undefined
  } else {
    return promptForCredential(cred, endpoint)
  }
}

async function getExternalCredential(input: Credential, token: string) {
  const path = getTrampolineEnvironmentPath(token)
  const cred = await fillCredential(input, path, getGcmEnv(token))
  if (cred) {
    info(`found credential for ${getCredentialUrl(cred)} in external helper`)
  }
  return cred
}

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

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
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

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```

**File:** app/src/lib/trampoline/find-account.ts (L31-51)
```typescript
export async function findGenericTrampolineAccount(
  trampolineToken: string,
  remoteUrl: string
) {
  const parsedUrl = new URL(remoteUrl)
  const endpoint = urlWithoutCredentials(remoteUrl)

  const login =
    parsedUrl.username === ''
      ? getGenericUsername(endpoint)
      : parsedUrl.username

  if (!login) {
    return undefined
  }

  const token = await memoizedGetGenericPassword(
    trampolineToken,
    endpoint,
    login
  )
```
