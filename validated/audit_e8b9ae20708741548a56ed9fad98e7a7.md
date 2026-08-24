### Title
Trampoline credential-helper token authorizes arbitrary-host credential requests, not just the current Git operation - ([File: app/src/lib/trampoline/trampoline-server.ts])

### Summary
The external report's core defect is a bearer credential (`_checkSig`'s signature) that authorizes an action without being bound to the specific context it was issued for (contract instance/chain), enabling reuse in a context the issuer never intended. GitHub Desktop's trampoline mechanism has the same structural flaw: `DESKTOP_TRAMPOLINE_TOKEN` is a bearer token checked only for set-membership by `isValidTrampolineToken`, with no binding to the command identifier, the remote host being authenticated, or the git operation that requested it. Since this token (and the local TCP port) is exposed via environment variables to every child process spawned during a Git operation — including attacker-controlled hooks/filters from a cloned or fetched repository — an untrusted script can reuse the token to invoke the `CredentialHelper` "get" command for a completely different host than the one being operated on, exfiltrating stored Git credentials.

### Finding Description
`withTrampolineEnv` injects `DESKTOP_PORT` and `DESKTOP_TRAMPOLINE_TOKEN` into the environment for every `git` invocation: [1](#0-0) 

This environment is inherited by all Git subprocesses in that operation's process tree — including Git hooks, `.gitattributes` clean/smudge filters, Git LFS, and submodule commands — all of which can be defined by the content of the repository being cloned/fetched, i.e., attacker-controlled.

On the server side, `TrampolineServer.processCommand` performs only a global validity check on the token, with no binding to which handler/identifier/host it is being used for: [2](#0-1) [3](#0-2) 

The `CredentialHelper` "get" handler accepts an arbitrary `Credential` (host/protocol/username) taken from the attacker-supplied `stdin` of the trampoline command, and looks up stored credentials purely based on that attacker-provided host string — not the host actually being operated on by the current `git` invocation: [4](#0-3) [5](#0-4) 

The token identifier and payload are parsed directly from the socket data with no origin/process/session binding beyond the raw token string: [6](#0-5) 

Because the trampoline binary connects to `127.0.0.1:$DESKTOP_PORT` and sends the token plus arbitrary parameters/stdin (see the trampoline README describing the TCP protocol), and any process holding the token can talk to the server directly: [7](#0-6) 

any process that inherits `DESKTOP_TRAMPOLINE_TOKEN` — including code from an untrusted repository executed as a Git hook/filter during clone/fetch/checkout — can bypass the "this token is for one specific remote/operation" assumption and request credentials for a completely different, attacker-chosen host. This is precisely the missing "domain separation" flaw called out in the original report: the authorization token proves only "a trampoline session is active," not "this action pertains to the endpoint/command it was minted for."

### Impact Explanation
An attacker who controls the content of a cloned/fetched repository (e.g., via a malicious `post-checkout` hook, `.gitattributes` smudge filter, or submodule) can, during a normal `git clone`/`fetch`/`checkout` performed by GitHub Desktop, read `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` from its own environment and connect to the local trampoline server to invoke `CREDENTIALHELPER get` for an arbitrary host (e.g., a different generic Git server the victim has previously stored credentials for via Desktop's generic credential store). This results in credential/token exfiltration for hosts unrelated to the repository being cloned — a direct violation of "the result is code execution, file write or read outside the repo, credential/token exfiltration."

### Likelihood Explanation
The precondition — a cloned/fetched repository that runs code as part of the Git object model (hooks, smudge/clean filters, LFS, submodules) — is a standard, unprivileged attacker capability against GitHub Desktop users who clone third-party repositories. No local access, malware, or leaked credentials are required beyond the victim performing an ordinary clone/fetch of the attacker's repository. However, this depends on the victim having previously stored *generic* (non-GitHub) Git credentials via Desktop's internal generic credential store, and on the token/port env vars actually being visible to the specific hook/filter mechanism used — this constrains exploitability somewhat, and I was not able to fully verify from the index alone whether all filter/hook invocation paths in Desktop's `dugite`/spawn wrapper strip or preserve this environment for all subprocess types (e.g., LFS smudge vs. custom hooks), so this should be validated empirically.

### Recommendation
Bind the trampoline token to the specific command identifier and target host/operation it was issued for (e.g., include the intended remote origin or command class in the token's associated session record, and reject `get`/`store`/`erase` requests whose target host does not match the origin of the git operation that requested the token). Additionally, scope tokens to a single command identifier (`AskPass` XOR `CredentialHelper`) instead of allowing any registered handler to accept the same token, and expire tokens immediately after the first command use rather than for the lifetime of the whole git operation.

### Proof of Concept
1. Attacker creates a public repository containing a Git hook (e.g., `post-checkout`) or a `.gitattributes` smudge filter that runs a small script.
2. Victim clones/fetches this repository using GitHub Desktop, which spawns `git` with `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` set per `withTrampolineEnv` (`app/src/lib/trampoline/trampoline-environment.ts:122-147`).
3. The hook/filter script (running as part of the clone/checkout) reads these two environment variables and opens a TCP connection to `127.0.0.1:$DESKTOP_PORT`.
4. It sends a trampoline command with `DESKTOP_TRAMPOLINE_IDENTIFIER=CREDENTIALHELPER`, the captured `DESKTOP_TRAMPOLINE_TOKEN`, parameter `get`, and stdin containing `protocol=https\nhost=some-other-generic-git-host.example.com\n`.
5. `TrampolineServer.processCommand` validates the token via `isValidTrampolineToken` (true, since the session is still active) and dispatches to `createCredentialHelperTrampolineHandler`, which looks up and returns any stored generic credential for `some-other-generic-git-host.example.com` (`app/src/lib/trampoline/trampoline-credential-helper.ts:93-135, 220-248`), which the attacker script now has in its stdout — despite the ongoing git operation having nothing to do with that host.

### Citations

**File:** app/src/lib/trampoline/trampoline-environment.ts (L122-147)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-183)
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

    const result = await handler(command).catch(e =>
      log.error('Error processing trampoline command', e)
    )

    if (result !== undefined) {
      socket.end(result)
    } else {
      socket.end()
    }
  }
```

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L13-16)
```typescript
/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
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

**File:** app/src/lib/trampoline/trampoline-command-parser.ts (L105-164)
```typescript
  /**
   * Returns a command.
   *
   * It will return null if the parser hasn't finished yet, or if the identifier
   * is missing or invalid.
   **/
  public toCommand(): ITrampolineCommand | null {
    if (this.hasFinished() === false) {
      const error = new Error(
        'The command cannot be generated if parsing is not finished'
      )
      this.logCommandCreationError(error)
      return null
    }

    const identifierString = this.environmentVariables.get(
      'DESKTOP_TRAMPOLINE_IDENTIFIER'
    )

    if (identifierString === undefined) {
      const error = new Error(
        `The command identifier is missing. Env variables received: ${Array.from(
          this.environmentVariables.keys()
        )}`
      )
      this.logCommandCreationError(error)
      return null
    }

    const identifier = parseEnumValue(
      TrampolineCommandIdentifier,
      identifierString
    )

    if (identifier === undefined) {
      const error = new Error(
        `The command identifier ${identifierString} is not supported`
      )
      this.logCommandCreationError(error)
      return null
    }

    const trampolineToken = this.environmentVariables.get(
      'DESKTOP_TRAMPOLINE_TOKEN'
    )

    if (trampolineToken === undefined) {
      const error = new Error(`The trampoline token is missing`)
      this.logCommandCreationError(error)
      return null
    }

    return {
      identifier,
      trampolineToken,
      parameters: this.parameters,
      environmentVariables: this.environmentVariables,
      stdin: this.stdin,
    }
  }
```

**File:** vendor/desktop-trampoline/README.md (L72-80)
```markdown
Desktop also sets these environment variables when spawning Git, as it's the
only way to pass information down to the authentication process:

- `DESKTOP_USERNAME` - the account associated with the current repository
- `DESKTOP_ENDPOINT` - the endpoint associated with the account

With this trampoline, all this info can be passed from GitHub Desktop to Git,
and then back to GitHub Desktop via a TCP socket when Git requires us the user
credentials, so Desktop can act based on that username and endpoint.
```
