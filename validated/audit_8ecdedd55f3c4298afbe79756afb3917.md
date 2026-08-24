This is confirmed: `getCredential` in `trampoline-credential-helper.ts` resolves which account's token to hand back purely from the `url`/`host` fields contained in the credential request payload itself, via `getCredentialUrl(cred)` and `findGitHubTrampolineAccount`, and the only gate on the whole exchange is `isValidTrampolineToken`, which merely checks Set membership with no binding to the specific remote/host the token was minted for. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Trampoline credential-helper token is not scoped to the operation's remote/host, allowing a malicious repository's build/filter process to harvest credentials for any signed-in GitHub account - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
The report's underlying flaw is a proof/message that is accepted as valid for any context because it omits a binding value (`chainId`) tying it to the intended domain. GitHub Desktop's trampoline mechanism has the same class of flaw: `DESKTOP_TRAMPOLINE_TOKEN` only proves "this request came from a git subprocess Desktop spawned for *some* operation," never which repository/remote/host it was spawned for. `getCredential` trusts the `host`/`url` fields supplied by whatever connects with a valid token and returns whichever stored account's origin matches, regardless of the repository the token was actually issued for.

### Finding Description
`withTrampolineEnv` mints a random UUID token, stores per-token bookkeeping (`isBackgroundTaskEnvironment`, `trampolineEnvironmentPath`) and injects `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` into the environment of the spawned `git` process. [5](#0-4)  That environment is inherited by every child process git spawns for that operation — including Git LFS smudge/clean filters and any filter/hook program configured by the *cloned repository itself* (e.g. `.gitattributes` filters, `core.fsmonitor`, hooks executed via `git -c ... `). Any process holding that token can open a TCP connection to `127.0.0.1:<DESKTOP_PORT>` and issue a `CREDENTIALHELPER get` command.

On the server side, `processCommand` only checks `isValidTrampolineToken(command.trampolineToken)`, i.e. Set membership, with no association back to which repository path or remote host the token was created for. [6](#0-5)  `getCredential` then derives the target endpoint entirely from attacker-suppliable `host`/`url`/`protocol` fields in the request payload via `getCredentialUrl`, and looks up whichever signed-in account matches that origin: [3](#0-2) [4](#0-3) 

Because the token is not cryptographically or logically bound to "the remote currently being fetched/pushed," a filter/hook program that runs as part of processing an attacker-authored repository (triggered merely by cloning/fetching/checking out that repository) can request `host=github.com` (or any GHE endpoint) instead of the legitimate remote the user actually intended, and the trampoline server will happily hand back the OAuth token for the user's real GitHub.com/GHE account — completely unrelated to the repository that spawned the request. This mirrors exactly the reported defect: a value ("chainId"/"which target this credential is for") that should scope the proof is missing, so a token minted for one context is honored in a different, attacker-chosen context.

### Impact Explanation
Successful exploitation exfiltrates the user's live GitHub.com/GHE OAuth access token to an attacker-controlled process, which can then be used remotely (outside Desktop) to read/write the victim's repositories, private data, and perform actions as the user — a direct credential-exfiltration and unauthorized-account-access outcome, without any prior malware or local access beyond what cloning/fetching a repository already implies.

### Likelihood Explanation
This requires the victim to clone/fetch/checkout a git repository that runs code as part of normal git plumbing (filter driver, smudge/clean, or a Git LFS-style hook) — a scenario already within Desktop's stated threat model of "attacker controls a cloned/fetched repository." The port and token are available in-process to any child git spawns for the operation, so no privilege escalation or pre-existing compromise is required beyond the repository interaction itself; likelihood is moderate-to-high given GitHub Desktop's routine use with third-party/forked repositories.

### Recommendation
Bind each trampoline token to the specific remote URL(s)/host(s) legitimately associated with the git operation it was issued for (store the expected remote alongside the token in `trampoline-tokens.ts`/`trampoline-environment.ts`), and reject `CREDENTIALHELPER get/store/erase` requests whose `host`/`url` do not match one of the remotes registered for that token — analogous to adding the missing `chainId` binding in the original report.

### Proof of Concept
1. Attacker publishes a repository containing a `.gitattributes` filter (or LFS-style clean/smudge hook) that, when invoked during `checkout`/`fetch`, reads `process.env.DESKTOP_TRAMPOLINE_TOKEN` and `process.env.DESKTOP_PORT`.
2. Victim clones this repository in GitHub Desktop; Desktop spawns `git` via `withTrampolineEnv`, injecting the token/port into the process tree used for the checkout, which also runs the filter.
3. The filter process opens a TCP connection to `127.0.0.1:<DESKTOP_PORT>` and sends a `CREDENTIALHELPER` command with `identifier=CREDENTIALHELPER`, `trampolineToken=<stolen token>`, and stdin `protocol=https\nhost=github.com\n\n` (or the user's actual GHE host) followed by the `get` action.
4. `trampoline-server.ts` accepts the token (valid, since it's the current session's token) and `getCredential` in `trampoline-credential-helper.ts` returns the victim's real GitHub.com/GHE `username`/`password` (OAuth token) because `findGitHubTrampolineAccount` matches purely by origin from the attacker-chosen `host` field, not the actual repository being cloned.
5. The filter process now has the victim's live GitHub access token and exfiltrates it over the network.

### Citations

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-105)
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
