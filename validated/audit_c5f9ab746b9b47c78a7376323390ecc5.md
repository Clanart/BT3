I have enough context to answer this now.

## Title
Cross-endpoint credential exfiltration via TCP trampoline server — no binding between `DESKTOP_TRAMPOLINE_TOKEN` and the operation's actual remote host - (File: `app/src/lib/trampoline/trampoline-credential-helper.ts`)

### Summary
`withTrampolineEnv` mints a single `DESKTOP_TRAMPOLINE_TOKEN` per git invocation and exposes it, together with `DESKTOP_PORT`, as environment variables to the spawned git process. [1](#0-0) 

Because environment variables are inherited by all descendant processes unless explicitly stripped, any child process spawned while that git command runs (filters, `clean`/`smudge` programs, and in particular Git LFS custom transfer agents configured via a repository-committed `.lfsconfig`) also receives `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT`. The C trampoline client itself performs no scoping — it simply forwards whatever token is present in the environment plus argv/stdin to the TCP server on `127.0.0.1:DESKTOP_PORT`. [2](#0-1) 

Server-side, `processCommand` only validates that the token is a currently-live token (i.e., that *some* git operation is in flight); it performs no check that the command's requested credential endpoint matches the endpoint the original operation is actually talking to: [3](#0-2) [4](#0-3) 

The credential-helper handler (`get`) resolves credentials purely from the `host`/`protocol`/`url` fields supplied in the request body (`stdin`), with **no correlation to the repository/remote that spawned the token**: [5](#0-4) [6](#0-5) 

`getGitHubCredential` calls `findGitHubTrampolineAccount(store, endpoint)` using only that attacker-suppliable `endpoint`, and if the user has any signed-in GitHub Desktop account matching it (e.g. `https://github.com`, or a GHE endpoint), returns that account's real login + OAuth token via `credWithAccount`.

### Finding Description
Any process that can read `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` from its environment — not just the actual git subcommand that Desktop intended to authenticate — can open a raw TCP connection to the trampoline server and issue a `CREDENTIALHELPER get` request with an arbitrary `host=`/`protocol=`/`url=` in the body. Because the server's only authorization check is `isValidTrampolineToken` (token liveness, not scope/host binding), and `getGitHubCredential`/`getGenericCredential` trust the endpoint from the request itself, the requester can obtain credentials for a completely different host than the one the fetch/clone operation is actually targeting.

The realistic vector is Git LFS: `.lfsconfig` and `.gitattributes` are tracked repository content, and LFS custom transfer agents (`lfs.customtransfer.<name>.path`) declared there are invoked automatically as part of `git lfs pull`/checkout during a clone/fetch, without any additional user confirmation, inheriting the parent git process's environment (including the trampoline token). This lets a malicious repository's custom-transfer script speak the trampoline wire protocol directly and request credentials for `github.com`, a GHE host, or any generic host the user has stored credentials for — none of which need be the repository's own remote.

### Impact Explanation
A malicious/compromised repository can exfiltrate the victim's real GitHub.com / GitHub Enterprise OAuth token (or generic stored git credentials for unrelated hosts) simply by being cloned or fetched, without any additional user interaction beyond the normal clone/fetch action. This is credential/token exfiltration reaching hosts entirely outside the scope of the operation the user initiated, matching the "credential/token exfiltration" impact category.

### Likelihood Explanation
Requires: (1) the victim to have GitHub Desktop signed in with a stored account, (2) the victim to clone/fetch an attacker-controlled repository that ships a `.lfsconfig` custom transfer agent (or another filter mechanism) and has Git LFS enabled/installed, and (3) that custom program to inherit the parent environment (default Node.js/child_process behavior for spawned commands, and default OS process semantics for LFS-launched helpers). No local access, admin rights, or leaked credentials are needed — the attacker only needs to control repository content, which is within scope.

### Recommendation
- Bind each trampoline token to the specific remote/host(s) the originating git operation is expected to talk to (passed down from `withTrampolineEnv`'s caller, e.g. the resolved `remote.origin.url` / LFS endpoint), and reject/prompt-fresh for `get` requests whose `host`/`url` doesn't match that binding, instead of trusting the token alone.
- Alternatively/additionally, don't inherit `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` into filter/custom-transfer child processes when they're not needed for the credential/askpass flow, or issue distinct per-target tokens rather than one token per whole invocation.
- Consider requiring the credential helper to know the *invoking program's* identity (e.g. that it's `git-credential-desktop` invoked directly by `git`) rather than accepting any TCP client with a valid token.

### Proof of Concept
1. Publish a repository containing a `.lfsconfig`:
   ```
   [lfs "https://attacker-controlled-origin.example/repo.git"]
     customtransfer.mytransfer.path = python3
     customtransfer.mytransfer.args = malicious_transfer.py
   ```
   and a `.gitattributes` marking a tracked file as `filter=lfs`, plus the referenced `malicious_transfer.py` script committed to the repo.
2. Victim clones the repo in GitHub Desktop while signed in to github.com.
3. During checkout, Git LFS invokes `malicious_transfer.py` as the custom transfer agent, inheriting `DESKTOP_TRAMPOLINE_TOKEN` and `DESKTOP_PORT` from the parent git process's environment set in [7](#0-6) .
4. `malicious_transfer.py` opens a TCP socket to `127.0.0.1:$DESKTOP_PORT`, writes the trampoline wire protocol frame identical to what `desktop-trampoline.c` produces for `CREDENTIALHELPER get`, but with stdin body `protocol=https\nhost=github.com\n\n`.
5. The trampoline server calls `createCredentialHelperTrampolineHandler` → `getCredential` → `getGitHubCredential`, finds the victim's real GitHub.com account, and returns `username=<login>&password=<oauth-token>` over the socket — to a script controlled entirely by the attacker's repository content, unrelated to the LFS server or repo origin. [8](#0-7)

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

**File:** vendor/desktop-trampoline/src/desktop-trampoline.c (L51-108)
```c
int runTrampolineClient(SOCKET *outSocket, int argc, char **argv, char **envp) {
  char *desktopPortString;

  desktopPortString = getenv("DESKTOP_PORT");

  if (desktopPortString == NULL) {
    fprintf(stderr, "ERROR: Missing DESKTOP_PORT environment variable\n");
    return 1;
  }

  unsigned short desktopPort = atoi(desktopPortString);

  SOCKET socket = openSocket();

  if (socket == INVALID_SOCKET) {
    printSocketError("ERROR: Couldn't create TCP socket");
    return 1;
  }

  *outSocket = socket;

  if (connectSocket(socket, desktopPort) != 0) {
    printSocketError("ERROR: Couldn't connect to 127.0.0.1:%d - Please make "
                     "sure you don't have an antivirus or firewall blocking "
                     "this connection.", desktopPort);
    return 1;
  }

  // Send the number of arguments (except the program name)
  char argcString[MAXIMUM_NUMBER_LENGTH];
  snprintf(argcString, MAXIMUM_NUMBER_LENGTH, "%d", argc - 1);
  WRITE_STRING_OR_EXIT("number of arguments", argcString);

  // Send each argument separated by \0
  for (int idx = 1; idx < argc; idx++) {
    WRITE_STRING_OR_EXIT("argument", argv[idx]);
  }

  // Get the number of environment variables
  char *validEnvVars[NUMBER_OF_VALID_ENV_VARS + 1];
  validEnvVars[0] = "DESKTOP_TRAMPOLINE_IDENTIFIER=" DESKTOP_TRAMPOLINE_IDENTIFIER;
  int envc = 1;
  for (char **env = envp; *env != 0; env++) {
    if (isValidEnvVar(*env)) {
      validEnvVars[envc] = *env;
      envc++;
    }
  }

  // Send the number of environment variables
  char envcString[MAXIMUM_NUMBER_LENGTH];
  snprintf(envcString, MAXIMUM_NUMBER_LENGTH, "%d", envc);
  WRITE_STRING_OR_EXIT("number of environment variables", envcString);

  // Send the environment variables
  for (int idx = 0; idx < envc; idx++) {
    WRITE_STRING_OR_EXIT("environment variable", validEnvVars[idx]);
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L240-248)
```typescript
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
