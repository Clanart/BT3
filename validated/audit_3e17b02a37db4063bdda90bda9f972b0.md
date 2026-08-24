## Finding [1](#0-0) 

### Title
Trampoline credential-helper server trusts any process holding a live (but host/identity-unbound) trampoline token, allowing exfiltration of GitHub tokens for an arbitrary host - (File: app/src/lib/trampoline/trampoline-server.ts, app/src/lib/trampoline/trampoline-credential-helper.ts, app/src/lib/trampoline/trampoline-environment.ts)

### Summary
The report's underlying bug class is: a security check ("session key is only valid in a specific, narrow execution context") is enforced only by the *absence* of a handler in the expected caller path, while a completely different, broadly-trusted mechanism (any plugin able to register a runtime validation function) can satisfy the same code path and unlock the privileged action for an unintended caller. The GitHub Desktop analog is the `TrampolineServer`, which is meant to hand out Git credentials only to Desktop's own `git`/`askpass` subprocess for the specific remote operation it was started for, but actually authorizes *any* local process that can present a still-live `DESKTOP_TRAMPOLINE_TOKEN`, for *any* host the caller chooses to claim in its request payload.

### Finding Description
`TrampolineServer.processCommand` only validates that the presented token exists in the global token set — it does not verify that the token belongs to the process/subprocess tree that was actually started for this git operation, nor that the requested credential `host`/`protocol` matches the remote the token was minted for: [2](#0-1) 

The token itself is a bare, short-lived UUID with no binding to a repository, remote host, or command identifier: [3](#0-2) 

It is exposed via `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` environment variables set around every remote git invocation (fetch/clone/push): [4](#0-3) 

Once a caller connects with a valid token, the `host`/`protocol` used to build the credential lookup URL is taken verbatim from the caller-supplied stdin payload — not from anything Desktop independently knows about the repository being operated on: [5](#0-4) 

`getCredential` then uses that attacker-chosen endpoint to look up a stored GitHub account and, if found, returns the real OAuth token in plaintext: [6](#0-5) [7](#0-6) 

This is structurally identical to the report's broken invariant: the intended gate ("only Git's askpass/credential-helper invocation for *this* remote may retrieve credentials") is not actually enforced by anything that binds the token to a specific host or command purpose — any other process that obtains the token can drive the same "runtime path" (`CredentialHelper` handler) that was only meant to be reachable from one context, exactly as an unrelated plugin in the report could satisfy `executeWithSessionKey()`'s runtime-validation slot and unlock functionality meant to be reachable only from the user-operation path.

Desktop's own hooks-interception code shows the team is aware that local, repo-adjacent subprocesses need isolated, connection-scoped tokens — it builds a *separate*, more tightly scoped mechanism (`process-proxy`, per-invocation `token`, `validateConnection`) specifically to avoid exposing git-hook subprocesses to sensitive channels: [8](#0-7) 

That more careful pattern is not applied to the credential-helper/askpass trampoline, which remains reachable by host-unbound token from any process that can read the token out of its (or its parent's) environment.

### Impact Explanation
If any process spawned as part of processing an attacker-influenced repository during a fetch/clone/checkout (e.g., a git filter, credential helper wrapper, or any other subprocess that inherits the git process's environment while `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` are set) opens a TCP connection to `127.0.0.1:$DESKTOP_PORT` and sends a `CREDENTIALHELPER get` command with `protocol=https\nhost=github.com`, it will receive the signed-in user's real GitHub username and OAuth token in plaintext — regardless of what remote the git operation was actually authenticating against. This is token exfiltration, matching the "credential/token exfiltration" impact category.

### Likelihood Explanation
Exploitation only requires the attacker to control content processed during a normal fetch/clone (no local/admin access, no leaked credentials, no social engineering beyond the user opening/cloning a repository) and requires a delivery mechanism that gets a subprocess spawned with the trampoline environment during that operation. I was able to fully verify the server-side defect (token not bound to host/purpose, host taken from caller input) from the indexed code, but I could **not** conclusively verify, within the available code, which specific attacker-controlled subprocess (e.g., a Git LFS custom transfer/extension command declared in a committed `.lfsconfig`, or another filter/diff driver) is guaranteed to inherit `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` during a clone of an untrusted repository. Git hooks are excluded (hooks are not versioned/cloned, and Desktop routes them through the separate, properly-scoped `process-proxy` mechanism shown above). This delivery-vector gap is the main uncertainty in turning this into a fully weaponized PoC and should be confirmed against the LFS/filter integration code before treating this as exploit-ready.

### Recommendation
- Bind trampoline tokens to the specific repository/remote (and ideally the specific command identifier: `ASKPASS` vs `CREDENTIALHELPER`) they were issued for, and reject `CredentialHelper`/`AskPass` requests whose `host`/`protocol` don't match the operation the token was minted for.
- Scope tokens to a single connection/use (similar to the `process-proxy` `validateConnection` pattern already used for hooks) rather than a set membership check with no caller/context binding.
- Audit which subprocesses spawned during git operations (filters, extensions, custom transfer agents) inherit `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN`, and strip these variables from environments handed to any subprocess that can be influenced by repository content.

### Proof of Concept
```
# Conceptual PoC (delivery mechanism for the untrusted subprocess is unverified — see Likelihood section):
# 1. Victim clones/fetches an attacker-controlled repository in GitHub Desktop while
#    signed in to github.com.
# 2. During the git operation, a repository-influenced subprocess inherits the
#    environment set in trampoline-environment.ts (DESKTOP_PORT, DESKTOP_TRAMPOLINE_TOKEN).
# 3. That subprocess connects directly to 127.0.0.1:$DESKTOP_PORT and sends the
#    trampoline wire protocol payload:
#      DESKTOP_TRAMPOLINE_IDENTIFIER=CREDENTIALHELPER
#      DESKTOP_TRAMPOLINE_TOKEN=<inherited token>
#      stdin: "get\nprotocol=https\nhost=github.com\n"
# 4. trampoline-server.ts only checks isValidTrampolineToken(token) (true) and
#    dispatches to the CredentialHelper handler.
# 5. trampoline-credential-helper.ts's getCredential() looks up the GitHub account
#    for "github.com" and returns username=<login>, password=<real OAuth token>
#    to the attacker-controlled subprocess.
```

### Citations

**File:** app/src/lib/trampoline/trampoline-server.ts (L162-176)
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
```

**File:** app/src/lib/trampoline/trampoline-tokens.ts (L1-16)
```typescript
const trampolineTokens = new Set<string>()

function requestTrampolineToken() {
  const token = crypto.randomUUID()
  trampolineTokens.add(token)
  return token
}

function revokeTrampolineToken(token: string) {
  trampolineTokens.delete(token)
}

/** Checks if a given trampoline token is valid. */
export function isValidTrampolineToken(token: string) {
  return trampolineTokens.has(token)
}
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L46-59)
```typescript
export const getCredentialUrl = (cred: Map<string, string>) => {
  const u = cred.get('url')
  if (u) {
    return new URL(u)
  }

  const protocol = cred.get('protocol') ?? ''
  const username = cred.get('username')
  const user = username ? `${encodeURIComponent(username)}@` : ''
  const host = cred.get('host') ?? ''
  const path = cred.get('path') ?? ''

  return new URL(`${protocol}://${user}${host}/${path}`)
}
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L123-146)
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
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L47-57)
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
}
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L94-99)
```typescript
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
  }
```

**File:** app/src/lib/hooks/with-hooks-env.ts (L47-71)
```typescript
  const token = crypto.randomUUID()
  const tmpHooksDir = await mkdtemp(join(tmpdir(), 'desktop-git-hooks-'))
  const hooksProxy = createHooksProxy(
    cwd =>
      memoizedGetShellEnv(
        getGitHookEnvShell(),
        cwd,
        // We always cache environment per token (i.e. per operation, e.g commit, apply, etc)
        // but we can optionally cache it over multiple operations in the same repository if the user
        // has enabled that setting.
        getCacheHooksEnv() ? 'global' : token
      ),
    tmpHooksDir,
    opts?.onHookProgress,
    opts?.onHookFailure
  )

  const server = createProxyProcessServer(
    conn =>
      hooksProxy(conn).catch(err => {
        log.error(`hooks proxy failed:`, err)
        conn.exit(1).catch(() => {})
      }),
    { validateConnection: async receivedToken => receivedToken === token }
  )
```
