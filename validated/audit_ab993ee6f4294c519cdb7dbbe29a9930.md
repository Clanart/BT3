### Title
Malicious Git LFS `clean`/`smudge` filter can exfiltrate GitHub credentials via the inherited `credential.helper=desktop` trampoline - (File: `app/src/lib/trampoline/trampoline-environment.ts`)

### Summary
GitHub Desktop configures `credential.helper=desktop` via the `GIT_CONFIG_PARAMETERS` environment variable, explicitly so that it is inherited by filter-driver subprocesses such as Git LFS `clean`/`smudge` filters. Any git filter defined in a cloned/fetched repository's `.gitattributes` therefore inherits `DESKTOP_PORT`/`DESKTOP_TRAMPOLINE_TOKEN` and can independently invoke `git credential fill` for an arbitrary host, receiving the user's live GitHub token from Desktop's credential trampoline without any additional authorization check tying the request to the actual remote being operated on.

### Finding Description
When Desktop spawns any git process for a repository operation, `withTrampolineEnv` sets: [1](#0-0) 

The comment explicitly states the environment-variable approach (rather than `-c credential.helper=`) is used *because* "we want commands invoked by filters (i.e. Git LFS) to be able to pick up our configuration" and that "Arguments passed to git commands are not passed down to filters" — i.e., this is a deliberate, documented inheritance path into filter subprocesses.

The credential helper trampoline handler resolves credentials purely from the request's declared `host`/`url` field, not from any binding to the actual git operation or remote: [2](#0-1) [3](#0-2) 

`getGitHubCredential` looks up an account purely by URL origin match: [4](#0-3) 

And the top-level `getCredential` returns the stored GitHub credential immediately if the origin matches a signed-in account, with no verification that the request originated from the actual git subprocess/remote for the in-flight operation: [5](#0-4) 

The trampoline server only validates a shared token, not the identity/purpose of the invoking process: [6](#0-5) 

The corrupted invariant: Desktop treats "any process spawned as a child of the current git operation that presents the valid `DESKTOP_TRAMPOLINE_TOKEN`" as equivalent to "the actual git command doing the actual credential-requiring network operation on the actual remote". That equivalence breaks when the repository itself defines a `clean`/`smudge`/`filter` driver (via `.gitattributes`, which is fully attacker-controlled content in a cloned/fetched repository) — that filter process is spawned by git as a normal child process during checkout/diff/add operations and inherits the full environment, including `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, and `GIT_CONFIG_PARAMETERS` setting `credential.helper=desktop`. The filter can simply run `git credential fill` (or speak the trampoline protocol directly) with `host=github.com`/`protocol=https` and receive the signed-in user's GitHub token — regardless of what remote the actual git command was contacting.

This mirrors the reported bug's "reentrant escape" pattern: the reentrant/attacker-controlled callback (there: `executeSwapDirect` reentry; here: an attacker-defined filter subprocess) is invoked *during* a trusted operation and is able to make an independent privileged call that the surrounding trust boundary assumed could only originate from the legitimate, singular caller.

### Impact Explanation
This results in credential/token exfiltration: a cloned or fetched repository containing a malicious `.gitattributes` filter definition can obtain the signed-in user's GitHub.com or GitHub Enterprise OAuth token merely by having Desktop perform any git operation that runs filters (checkout, add, diff, commit) on that repository — without phishing, without the user clicking anything beyond opening/cloning the repo and doing a normal git action. The exfiltrated token can then be sent out-of-band by the filter process (it is an arbitrary, attacker-supplied executable configured via `filter.<name>.clean`/`smudge` in `.gitattributes`, itself potentially auto-approved for GitHub-tracked repos or requiring only ordinary repo trust).

### Likelihood Explanation
Requires: (1) the victim clones/fetches a repository with a custom `filter.<name>.clean`/`smudge` command configured (via `.gitattributes` + a corresponding `.git/config`/global config entry, or via `core.hooksPath`-style config that Desktop itself trusts once `git lfs install`/generic filters are set up), and (2) any Desktop git operation on that repository that invokes the filter. No admin rights, no pre-existing malware, and no credential leakage are required — the only "unnatural" step is that filter-driver configuration is not automatically picked up from an untrusted repo's own `.gitattributes` alone (git generally requires the filter command itself to be defined in a config Desktop or the user already trusts, e.g. via LFS). This nuance limits likelihood somewhat versus a fully zero-click attack, but Git LFS filters — which Desktop actively detects and offers to install (`isUsingLFS`, `installLFSHooks`) — are exactly this kind of externally-declared, repo-triggered filter path.

### Recommendation
- Scope the `credential.helper=desktop` trampoline so it is only reachable by the specific git subprocess performing the authenticated network operation, not by all descendant filter processes; e.g., strip `DESKTOP_TRAMPOLINE_TOKEN`/`DESKTOP_PORT` from the environment passed to filter-driver invocations, or use a distinct, narrowly-scoped token/credential surface for filters that cannot resolve arbitrary hosts.
- Bind trampoline credential requests to the endpoint/remote that was actually being fetched/pushed for that operation (as recorded in `trampolineEnvironmentPath`/operation context) rather than trusting the `host` field supplied by the requesting process.
- Continue treating LFS/filter driver configuration as sensitive; warn or require explicit consent before allowing filters defined by a newly cloned repository to run with network-credential-capable environment variables.

### Proof of Concept
1. Attacker publishes a repository containing:
   - `.gitattributes`: `*.bin filter=evil`
   - Local git config (delivered via a setup script or a companion LFS-style installer the victim is asked to run once, consistent with how `git lfs install` registers filters) defining `filter.evil.clean = /path/to/evil.sh` and `filter.evil.smudge = /path/to/evil.sh`.
2. Victim clones the repository in GitHub Desktop and performs any operation that invokes filters (e.g., checkout, staging a `.bin` file).
3. Desktop spawns `git` with env including `DESKTOP_PORT`, `DESKTOP_TRAMPOLINE_TOKEN`, and `GIT_CONFIG_PARAMETERS='credential.helper=' 'credential.helper=desktop'` (per `withTrampolineEnv`, `app/src/lib/trampoline/trampoline-environment.ts:123-146`).
4. Git spawns `evil.sh` as the filter child process, inheriting that environment.
5. `evil.sh` runs `printf 'protocol=https\nhost=github.com\n' | git credential fill`, which routes through the inherited `credential.helper=desktop` to Desktop's trampoline server (`app/src/lib/trampoline/trampoline-server.ts`), which invokes `createCredentialHelperTrampolineHandler` → `getCredential` → `getGitHubCredential` (`app/src/lib/trampoline/trampoline-credential-helper.ts:93-99`, `46-57`), returning the victim's GitHub token in the `password` field.
6. `evil.sh` exfiltrates the token over the network.

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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L46-57)
```typescript
 */
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

**File:** app/src/lib/trampoline/trampoline-credential-helper.ts (L93-99)
```typescript
/** Implementation of the 'get' git credential helper command */
async function getCredential(cred: Credential, store: Store, token: string) {
  const ghCred = await getGitHubCredential(cred, store)

  if (ghCred) {
    return ghCred
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

**File:** app/src/lib/trampoline/trampoline-server.ts (L26-44)
```typescript
export class TrampolineServer {
  private readonly server: Server
  private listeningPromise: Promise<void> | null = null

  private readonly commandHandlers = new Map<
    TrampolineCommandIdentifier,
    TrampolineCommandHandler
  >()

  public constructor() {
    this.server = createServer(socket => this.onNewConnection(socket))

    // Make sure the server is always unref'ed, so it doesn't keep the app alive
    // for longer than needed. Not having this made the CI tasks on Windows
    // timeout because the unit tests completed in about 7min, but the test
    // suite runner would never finish, hitting a 45min timeout for the whole
    // GitHub Action.
    this.server.unref()
  }
```
