### Title
Trampoline credential-helper does not bind the requested credential URL to the git remote of the operation, allowing cross-origin credential disclosure - ([File: app/src/lib/trampoline/trampoline-credential-helper.ts])

### Summary
The external report's core flaw is that a signature/authorization artifact was validated only against generic, reusable checks (deadline + claimant) but not bound to the specific context (wave) it was meant to authorize, letting it be replayed across unrelated contexts. The Desktop analog is the `DESKTOP_TRAMPOLINE_TOKEN` used by the git credential-helper trampoline: the token is validated only for *liveness* (`isValidTrampolineToken`), and the credential lookup (`getGitHubCredential`/`findGitHubTrampolineAccount`) is keyed purely off whatever `url`/`host` the credential request claims, with no check that this endpoint matches the actual remote (`trampolineEnvironmentPath`/repository) for which the token was minted.

### Finding Description
`isValidTrampolineToken` only checks set membership, not which repository/operation the token belongs to: [1](#0-0) 

The trampoline server accepts *any* command carrying a currently live token and dispatches it to the registered handler without verifying it originated from the git subprocess tree that was actually spawned for a specific repository/remote: [2](#0-1) 

`withTrampolineEnv` stores the repo path (`trampolineEnvironmentPath`) and background-task flag keyed by token, but these are only used for prompting/UI decisions and default paths — never to validate that a credential request's `url`/`host` matches the remote being operated on: [3](#0-2) 

The credential-helper handler resolves credentials purely from the attacker/git-supplied `cred` map (parsed from stdin) via `getCredentialUrl`, with no cross-check against the actual configured remote of the operation identified by the token: [4](#0-3) [5](#0-4) 

`findGitHubTrampolineAccount` only compares URL *origin* equality against stored accounts, returning the user's GitHub token for whichever origin is requested: [6](#0-5) 

Any process that inherits the git subprocess environment for a given operation (`DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`) — e.g. a smudge/clean/diff/merge filter driver, or an LFS/custom content filter invoked while cloning or fetching from an attacker-controlled remote configured via `.gitattributes`/`.lfsconfig` — can independently connect to the trampoline socket and issue a `CREDENTIALHELPER get` request with a forged `url=https://github.com` (or `https://<GHE-endpoint>`), because the token is still "live" for the duration of that git invocation and the server performs no context binding beyond liveness.

### Impact Explanation
This lets an attacker-controlled repository (via a filter/driver invoked during a legitimate clone/fetch/checkout of that repo) request and receive the signed-in user's real GitHub.com/GHE OAuth token by simply claiming to want credentials for `github.com`, i.e., unauthorized credential/token exfiltration — matching the "Valid Impact" bucket (attacker-controlled repo → credential exfiltration) called out in the task.

### Likelihood Explanation
Requires only cloning/fetching an attacker-supplied repository configured with a content filter/driver that runs in-process with the trampoline environment variables; no local/admin access or pre-existing malware needed. It relies on the same "context-free validity" root cause as the reported smart-contract bug: a reusable authorization token validated without binding to the operation/remote it was issued for. Full exploitability (whether a filter driver can be triggered purely from a fresh clone without prior local config, e.g. via `.gitattributes` referencing a filter the user already has configured, or via git-lfs's default global filter) is not fully confirmed from the indexed code alone and would need additional verification in a live environment.

### Recommendation
Bind the trampoline token to the specific operation's remote context, and enforce that check in the credential-helper handler: reject (or require explicit generic-credential prompting for) any `get` request whose `url`/`host` origin does not match the remote(s) registered for that token's `trampolineEnvironmentPath`/repository, instead of trusting the requested origin alone.

### Proof of Concept
Not independently executed; conceptual PoC based on code inspection:
1. User clones/fetches an attacker-controlled repository that configures a content filter (e.g. via `.gitattributes`/`.lfsconfig`) which spawns an auxiliary process inheriting `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` from the live git operation.
2. That auxiliary process connects to `127.0.0.1:<DESKTOP_PORT>` and sends a `CREDENTIALHELPER` command with `url=https://github.com`. [7](#0-6) 
3. `processCommand` validates only token liveness and routes to `createCredentialHelperTrampolineHandler`, which calls `getGitHubCredential` → `findGitHubTrampolineAccount`, returning the signed-in GitHub account's `login`/`token` regardless of the actual repo being cloned. [8](#0-7)

### Citations

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L13-16)
```typescript
/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
}
```

**File:** app/src/lib/trampoline/trampoline-server.ts (L109-146)
```typescript
  private onNewConnection(socket: Socket) {
    const parser = new TrampolineCommandParser()

    // Messages coming from the trampoline client will be separated by \0
    socket.pipe(split2(/\0/)).on('data', data => {
      this.onDataReceived(socket, parser, data)
    })

    socket.on('error', this.onClientError)
  }

  private onDataReceived(
    socket: Socket,
    parser: TrampolineCommandParser,
    data: Buffer
  ) {
    const value = data.toString('utf8')

    try {
      parser.processValue(value)
    } catch (error) {
      log.error('Error processing trampoline data', error)
      socket.end()
      return
    }

    if (!parser.hasFinished()) {
      return
    }

    const command = parser.toCommand()
    if (command === null) {
      socket.end()
      return
    }

    this.processCommand(socket, command)
  }
```

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-172)
```typescript
  private async processCommand(socket: Socket, command: ITrampolineCommand) {
    if (!isValidTrampolineToken(command.trampolineToken)) {
      throw new Error('Tried to use invalid trampoline token')
    }

    const handler = this.commandHandlers.get(command.identifier)

    if (handler === undefined) {
      socket.end()
      return
    }
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L93-147)
```typescript
export async function withTrampolineEnv<T>(
  fn: (env: object) => Promise<T>,
  path: string,
  isBackgroundTask = false,
  customEnv?: Record<string, string | undefined>
): Promise<T> {
  const sshEnv = await getSSHEnvironment()

  return withTrampolineToken(async token => {
    isBackgroundTaskEnvironment.set(token, isBackgroundTask)
    trampolineEnvironmentPath.set(token, path)

    const existingGitEnvConfig =
      customEnv?.['GIT_CONFIG_PARAMETERS'] ??
      process.env['GIT_CONFIG_PARAMETERS'] ??
      ''

    const gitEnvConfigPrefix =
      existingGitEnvConfig.length > 0 ? `${existingGitEnvConfig} ` : ''

    // The code below assumes a few things in order to manage SSH key passphrases
    // correctly:
    // 1. `withTrampolineEnv` is only used in the functions `git` (core.ts)
    // 2. Those two functions always thrown an error when something went wrong,
    //    and just return a result when everything went fine.
    //
    // With those two premises in mind, we can safely assume that right after
    // `fn` has been invoked, we can store the SSH key passphrase for this git
    // operation if there was one pending to be stored.
    try {
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L50-57)
```typescript
async function getGitHubCredential(cred: Credential, store: AccountsStore) {
  const endpoint = `${getCredentialUrl(cred)}`
  const account = await findGitHubTrampolineAccount(store, endpoint)
  if (account) {
    info(`found GitHub credential for ${endpoint} in store`)
  }
  return credWithAccount(cred, account)
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-135)
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

  // GitHub.com/GHE creds are only stored internally
  if (endpointKind !== 'generic') {
    return undefined
  }

  return useExternalCredentialHelper()
    ? getExternalCredential(cred, token)
    : getGenericCredential(cred, token)
}
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
