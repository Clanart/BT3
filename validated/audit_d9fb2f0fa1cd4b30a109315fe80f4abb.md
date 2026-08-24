### Title
Post-checkout hook can hijack the trampoline TCP session to exfiltrate an account's stored OAuth token, since the trampoline protocol authenticates only by a short-lived shared token, not by the calling git command or target host — (File: `app/src/lib/git/worktree.ts`, `app/src/lib/git/core.ts`, `app/src/lib/trampoline/trampoline-environment.ts`, `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`git()` in `core.ts` unconditionally merges `withTrampolineEnv`'s environment (`DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, `GIT_CONFIG_PARAMETERS` setting `credential.helper=desktop`) into the environment for every git invocation, including `worktree add`. [1](#0-0)  `addWorktree` calls `git()` without `interceptHooks`, which means `withHooksEnv` skips the hooks-proxy sandbox entirely and lets the repository's real hooks run with the parent process's environment unmodified. [2](#0-1) [3](#0-2)  Because git hooks inherit the environment of the invoking git process, a `post-checkout` hook shipped inside `.git/hooks` of a cloned repository (triggered by `git worktree add`) will receive `DESKTOP_PORT` and `DESKTOP_TRAMPOLINE_TOKEN` and can use them to speak the trampoline wire protocol directly to `TrampolineServer`.

### Finding Description
The trampoline server accepts a raw text protocol over a local TCP socket and validates only that the supplied token is currently active — it does not verify which git subcommand originated the request, nor which host/remote the request is legitimately for. [4](#0-3) [5](#0-4)  The credential-helper `get` handler resolves credentials purely by matching the `host`/`url` field the client supplies in the credential blob against the signed-in `AccountsStore` entries — there is no binding to the actual repository/remote that spawned the operation. [6](#0-5) [7](#0-6)  The wire format is a simple length/count-delimited protocol (parameter count, parameters, env var count, env vars including `DESKTOP_TRAMPOLINE_TOKEN`, then stdin) that any process can trivially reconstruct. [8](#0-7)  Combined, a malicious `post-checkout` hook that inherits `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` can open its own connection to `127.0.0.1:$DESKTOP_PORT`, send a `CREDENTIALHELPER get` command with `host=github.com` (or any host matching a signed-in account's endpoint) in the stdin body, and receive the account's username/OAuth token back over the socket — all without git itself ever making an HTTPS request to that host.

### Impact Explanation
If exploitable, this allows exfiltration of the user's GitHub OAuth token (or GHE token) from a repository the user merely clones and adds as a worktree — no push/fetch to an attacker remote is required, since the hook talks straight to the local trampoline server. That token can then be used by the hook to access the victim's GitHub account/API directly. This matches the "credential/token exfiltration" impact category for unprivileged, attacker-controlled repository content.

### Likelihood Explanation
Exploitability hinges on two verifiable facts I confirmed and one I could not fully verify:
- Confirmed: `addWorktree` does not set `interceptHooks`, so the hook-sandboxing proxy (`with-hooks-env.ts`) is bypassed for worktree operations, unlike `commit.ts`, `merge.ts`, `pull.ts`, and `push.ts` which do pass `interceptHooks`. [3](#0-2) [9](#0-8) 
- Confirmed: the trampoline env (`DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`) is merged into the exact `env` object passed to `exec()` for the `git worktree add` invocation. [10](#0-9) 
- Confirmed: the trampoline server's only authentication is the shared token, and the credential lookup is keyed on attacker-suppliable host data, not on the actual remote of the repository. [4](#0-3) [11](#0-10) 
- Unverified in this session (would need runtime/dugite behavior confirmation): whether Node's `child_process`/`dugite`'s `exec` truly passes this `env` object down such that git then passes the *same* full env to hook child processes unmodified (git normally does inherit its own process environment into hooks, which is standard git behavior, but I did not trace dugite's exec option handling in this pass), and whether `getGitHubCredential`'s prompt-suppression logic (`getIsBackgroundTaskEnvironment`, sign-in prompts for GitHub hosts when no account is cached) meaningfully limits real-world exploitation for GitHub.com/GHE accounts versus only "generic" credentials. For a signed-in GitHub.com account, `getGitHubCredential` returns the credential without any prompt, since it only checks `findGitHubTrampolineAccount` matching by origin — this path appears to require no user interaction. [6](#0-5) [12](#0-11)  The proof idea's specific detail of requesting `evil.example` is likely a misstatement — to actually receive the real OAuth token, the hook would need to request a `host` matching a signed-in account's endpoint (e.g. `github.com`), not an attacker-chosen arbitrary host; requesting `evil.example` would only succeed in exfiltrating something if the user happens to have a generic/enterprise credential stored for that exact host.

### Recommendation
- Scope worktree/checkout-triggering git operations that can run untrusted repository hooks (`addWorktree`, and any other `git()` call that can invoke hooks) through the same `interceptHooks` sandboxing mechanism used by `commit.ts`/`merge.ts`/`pull.ts`/`push.ts`, so that hooks never see `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` directly.
- Independently, harden the trampoline server itself: bind credential responses to the specific git operation/session that requested them (e.g., single-use tokens scoped to one expected host, or validating that the requesting process is the actual git subprocess rather than any local process holding the token) rather than trusting any request bearing a valid ambient token.

### Proof of Concept
1. Attacker publishes a repository containing `.git/hooks/post-checkout` (executable) that:
   - Reads `DESKTOP_PORT` and `DESKTOP_TRAMPOLINE_TOKEN` from its environment.
   - Opens a TCP connection to `127.0.0.1:$DESKTOP_PORT`.
   - Sends the trampoline protocol payload: parameter count `1`, parameter `get`, env count `2`, `DESKTOP_TRAMPOLINE_IDENTIFIER=CREDENTIALHELPER`, `DESKTOP_TRAMPOLINE_TOKEN=<token>`, then stdin `protocol=https\nhost=github.com\n\n`.
   - Reads the socket response (`username=...\npassword=<oauth-token>\n`) and POSTs it to an attacker-controlled server.
2. Victim clones/opens this repository in GitHub Desktop and creates a worktree from it (`addWorktree`), which invokes `git worktree add`.
3. Since `addWorktree` doesn't set `interceptHooks`, the `post-checkout` hook fires with `core.ts`'s trampoline env inherited, and the hook exfiltrates the signed-in account's OAuth token as described.

<br>

Note: I was not able to trace dugite's/Node's exact `exec` implementation in this session to fully confirm the environment inheritance chain from `git()`'s `exec()` call down into git's hook child-process spawning; this is standard git/OS behavior but wasn't independently re-verified against this specific dugite version, and I did not find or read `vendor/desktop-trampoline` client code confirming that its `get` requests are format-identical to what I reconstructed from `trampoline-command-parser.ts` for the credential-helper flow (only the askpass flow's raw C client was retrieved). If precise verification of these boundary details is needed, a Devin session with full filesystem/build access should confirm them.

### Citations

**File:** app/src/lib/git/core.ts (L276-295)
```typescript
  return withHooksEnv(
    hooksEnv =>
      withTrampolineEnv(
        async env => {
          const commandName = `${name}: git ${args.join(' ')}`

          const result = await GitPerf.measure(commandName, () =>
            exec(args, path, {
              ...opts,
              env: {
                // Explicitly set TERM to 'dumb' so that if Desktop was launched
                // from a terminal or if the system environment variables
                // have TERM set Git won't consider us as a smart terminal.
                // See https://github.com/git/git/blob/a7312d1a2/editor.c#L11-L15
                TERM: 'dumb',
                ...opts.env,
                ...hooksEnv,
                ...env,
              },
            })
```

**File:** app/src/lib/hooks/with-hooks-env.ts (L29-36)
```typescript
export async function withHooksEnv<T>(
  fn: (env: Record<string, string | undefined> | undefined) => Promise<T>,
  path: string,
  opts: IGitExecutionOptions | undefined
): Promise<T> {
  if (!opts?.interceptHooks || !getHooksEnvEnabled()) {
    return fn(opts?.env)
  }
```

**File:** app/src/lib/git/worktree.ts (L120-143)
```typescript
export async function addWorktree(
  repository: Repository,
  path: string,
  options: {
    /** Branch name used with -b (create new branch) */
    readonly createBranch?: string
    /** Commit-ish to check out (branch name, ref, or SHA) */
    readonly commitish?: string
  } = {}
): Promise<void> {
  const args = ['worktree', 'add']

  if (options.createBranch) {
    args.push('-b', options.createBranch)
  }

  args.push(path)

  if (options.commitish) {
    args.push(options.commitish)
  }

  await git(args, repository.path, 'addWorktree')
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

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L13-16)
```typescript
/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
}
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-135)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-command-parser.ts (L42-103)
```typescript
  public processValue(value: string) {
    switch (this.state) {
      case TrampolineCommandParserState.ParameterCount:
        this.parameterCount = parseInt(value)

        if (this.parameterCount > 0) {
          this.state = TrampolineCommandParserState.Parameters
        } else {
          this.state = TrampolineCommandParserState.EnvironmentVariablesCount
        }

        break

      case TrampolineCommandParserState.Parameters:
        this.parameters.push(value)
        if (this.parameters.length === this.parameterCount) {
          this.state = TrampolineCommandParserState.EnvironmentVariablesCount
        }
        break

      case TrampolineCommandParserState.EnvironmentVariablesCount:
        this.environmentVariablesCount = parseInt(value)

        if (this.environmentVariablesCount > 0) {
          this.state = TrampolineCommandParserState.EnvironmentVariables
        } else {
          this.state = TrampolineCommandParserState.Stdin
        }

        break

      case TrampolineCommandParserState.EnvironmentVariables:
        // Split after the first '='
        const match = /([^=]+)=(.*)/.exec(value)

        if (
          match === null ||
          // Length must be 3: the 2 groups + the whole string
          match.length !== 3
        ) {
          throw new Error(`Unexpected environment variable format: ${value}`)
        }

        const variableKey = match[1]
        const variableValue = match[2]

        this.environmentVariables.set(variableKey, variableValue)

        if (this.environmentVariables.size === this.environmentVariablesCount) {
          this.state = TrampolineCommandParserState.Stdin
        }
        break
      case TrampolineCommandParserState.Stdin:
        this.stdin = value
        this.state = TrampolineCommandParserState.Finished
        break
      case TrampolineCommandParserState.Finished:
        throw new Error(`Received value when in Finished`)
      default:
        assertNever(this.state, `Invalid state: ${this.state}`)
    }
  }
```
