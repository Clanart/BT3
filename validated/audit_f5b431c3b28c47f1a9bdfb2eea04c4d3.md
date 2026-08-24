### Title
SSH askpass trampoline auto-supplies cached key passphrase/password based on prompt-text matching only, without verifying the destination host of the current git operation - ([File: app/src/lib/trampoline/trampoline-askpass-handler.ts])

### Summary
The `RocketStorage` report's broken invariant is: a privileged action is authorized by checking a *partial, spoofable identity* (`tx.origin == guardian`) instead of the *actual calling context* (`msg.sender`, i.e. which contract is really making the call). GitHub Desktop's SSH askpass trampoline has the same class of flaw: it authorizes release of a cached secret (SSH key passphrase or SSH user password) purely by pattern-matching the *text of the ssh prompt* (a key file path or a `user@host` string), without cross-checking that the destination the current git subprocess is *actually* talking to is the same trusted destination the secret was originally issued for.

### Finding Description
`createAskpassTrampolineHandler` in `app/src/lib/trampoline/trampoline-askpass-handler.ts:148-171` dispatches purely on string matching of the prompt text supplied by the spawned `ssh`/git process: [1](#0-0) 

For a passphrase prompt, `handleSSHKeyPassphrase` extracts only the key **path** from the prompt (`Enter passphrase for key '<path>': `) and looks up a cached passphrase keyed by a hash of that file: [2](#0-1) [3](#0-2) 

For a password prompt, `handleSSHUserPassword` extracts only the `user@host`-looking string from the prompt and looks the password up by that string: [4](#0-3) [5](#0-4) 

Neither handler receives, nor checks, which remote/host the *current* git operation is actually connecting to. The only per-operation context passed through is `operationGUID`/`trampolineToken`, which is used solely to remember "most recent" credentials for later cleanup — not to verify the target host: [6](#0-5) 

The one place a host identity actually is validated — SSH host-key trust-on-first-use — auto-accepts only `github.com`'s pinned fingerprint, and otherwise defers to a one-time user prompt via `trampolineUIHelper.promptAddingSSHHost` (`handleSSHHostAuthenticity`, lines 18-53). Once a host has been trusted once (added to known_hosts, exactly like Rocket Pool's `storageInit` flag flipping to `true`), every *subsequent* SSH session — including one that a malicious/untrusted cloned or fetched repository silently triggers (e.g., via a crafted submodule URL, `core.sshCommand`, or another git hook that runs a fetch/pull to an arbitrary `ssh://` URL) — will pass the host-authenticity check and reach the passphrase/password lookup, which then blindly returns the cached secret if the *file path* or *string* happens to match a previously-used one, with no re-verification that this operation is destined for the same trusted party the secret was captured for.

This mirrors the report's exploit shape precisely: a value trusted once during a "setup"/"trust-establishment" phase (guardian call / SSH host TOFU) is later reused unconditionally by an internal component (`RocketStorage` write / askpass trampoline) that checks only a narrow, attacker-influenceable signal (`tx.origin` / prompt string) instead of validating the actual current caller/destination.

### Impact Explanation
If a malicious or compromised repository that a user clones/fetches (per the allowed threat model) causes git to spawn an SSH connection whose askpass prompt happens to reuse a previously-cached identity string (e.g. the user's default SSH key path, which ssh will try automatically when no `IdentityFile` is specified, or a `user@host` combination already cached from legitimate use of that same host by the user), Desktop will silently supply the cached passphrase/password to authenticate that connection — without any dialog, without confirming which repository/operation triggered it, and without confirming the destination matches the original context. This can cause the user's SSH key to be used to authenticate to a destination they did not knowingly approve for that specific operation, and, when the string collision includes the real trusted host (e.g., a shared internal git host with many repos, or a maliciously crafted submodule pointing back at a genuinely trusted host under a different path), it silently authorizes actions using the user's credentials against a repository/path the user never reviewed — analogous to the guardian's trusted call being hijacked to make an untrusted storage write.

### Likelihood Explanation
Medium-low. It requires: (1) the user to have previously cached an SSH passphrase/password via Desktop's askpass flow (opt-in "remember" checkbox), and (2) a cloned/fetched repository to trigger an additional git/SSH invocation (submodule, hook, or a second remote) whose askpass prompt text collides with a cached key path or `user@host` string. The key-path collision case is the more realistic path since ssh's default identity file list is small and predictable, making unintentional or attacker-engineered collisions plausible without any special local access — consistent with the required "unprivileged, attacker controls a cloned/fetched repository" threat model.

### Recommendation
- **Short term**: Document that cached SSH passphrases/passwords are released based on ssh prompt-text matching only, and that any operation on an untrusted cloned/fetched repository capable of spawning additional `ssh`/git subprocesses (submodules, hooks, `core.sshCommand`) can trigger reuse of previously cached secrets against a host not explicitly reviewed for that operation.
- **Long term**: Bind cached SSH secrets to the specific remote/host (and ideally the specific repository) they were captured for, and re-prompt (or require explicit re-confirmation) whenever the destination host of the current git invocation cannot be independently verified to match the host the secret was issued for — do not rely solely on the ssh-emitted prompt string as the trust boundary.

### Proof of Concept
1. In Desktop, clone or authenticate to a legitimate SSH remote using a passphrase-protected default SSH key (e.g. `~/.ssh/id_rsa`), choosing "remember passphrase" so it's cached (`setSSHKeyPassphrase`, keyed by file hash of `~/.ssh/id_rsa`).
2. Clone/fetch an attacker-controlled public repository whose `.gitmodules` (or a hook) defines a submodule/remote pointing at `ssh://attacker-controlled-host/...` with no explicit `IdentityFile`, so `ssh` falls back to trying the user's default identity `~/.ssh/id_rsa` and emits the prompt `Enter passphrase for key '~/.ssh/id_rsa': `.
3. When Desktop runs `git submodule update` (or any operation touching that remote), `createAskpassTrampolineHandler` matches the prompt, `handleSSHKeyPassphrase` hashes the same key path, finds the cached passphrase, and returns it automatically — decrypting and using the user's private key to authenticate to the attacker's host without any prompt to the user, because the handler never checks that the destination host matches the one the passphrase was cached for.

### Citations

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L55-83)
```typescript
async function handleSSHKeyPassphrase(
  operationGUID: string,
  prompt: string
): Promise<string | undefined> {
  const promptRegex = /^Enter passphrase for key '(.+)': $/

  const matches = promptRegex.exec(prompt)
  if (matches === null || matches.length < 2) {
    return undefined
  }

  let keyPath = matches[1]

  // The ssh bundled with Desktop on Windows, for some reason, provides Unix-like
  // paths for the keys (e.g. /c/Users/.../id_rsa). We need to convert them to
  // Windows-like paths (e.g. C:\Users\...\id_rsa).
  if (__WIN32__ && /^\/\w\//.test(keyPath)) {
    const driveLetter = keyPath[1]
    keyPath = keyPath.slice(2)
    keyPath = `${driveLetter}:${keyPath}`
  }

  const storedPassphrase = await getSSHKeyPassphrase(keyPath)
  if (storedPassphrase !== null) {
    // Keep this stored passphrase around in case it's not valid and we need to
    // delete it if the git operation fails to authenticate.
    await setMostRecentSSHKeyPassphrase(operationGUID, keyPath)
    return storedPassphrase
  }
```

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L111-127)
```typescript
async function handleSSHUserPassword(operationGUID: string, prompt: string) {
  const promptRegex = /^(.+@.+)'s password: $/

  const matches = promptRegex.exec(prompt)
  if (matches === null || matches.length < 2) {
    return undefined
  }

  const username = matches[1]

  const storedPassword = await getSSHUserPassword(username)
  if (storedPassword !== null) {
    // Keep this stored password around in case it's not valid and we need to
    // delete it if the git operation fails to authenticate.
    setMostRecentSSHUserPassword(operationGUID, username)
    return storedPassword
  }
```

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L151-168)
```typescript
  (accountsStore: AccountsStore) => async command => {
    if (command.parameters.length !== 1) {
      return undefined
    }

    const firstParameter = command.parameters[0]

    if (firstParameter.startsWith('The authenticity of host ')) {
      return handleSSHHostAuthenticity(command.trampolineToken, firstParameter)
    }

    if (firstParameter.startsWith('Enter passphrase for key ')) {
      return handleSSHKeyPassphrase(command.trampolineToken, firstParameter)
    }

    if (firstParameter.endsWith("'s password: ")) {
      return handleSSHUserPassword(command.trampolineToken, firstParameter)
    }
```

**File:** app/src/lib/ssh/ssh-key-passphrase.ts (L13-26)
```typescript
async function getHashForSSHKey(keyPath: string) {
  return getFileHash(keyPath, 'sha256')
}

/** Retrieves the passphrase for the SSH key in the given path. */
export async function getSSHKeyPassphrase(keyPath: string) {
  try {
    const fileHash = await getHashForSSHKey(keyPath)
    return TokenStore.getItem(SSHKeyPassphraseTokenStoreKey, fileHash)
  } catch (e) {
    log.error('Could not retrieve passphrase for SSH key:', e)
    return null
  }
}
```

**File:** app/src/lib/ssh/ssh-user-password.ts (L11-19)
```typescript
/** Retrieves the password for the given SSH username. */
export async function getSSHUserPassword(username: string) {
  try {
    return TokenStore.getItem(SSHUserPasswordTokenStoreKey, username)
  } catch (e) {
    log.error('Could not retrieve passphrase for SSH key:', e)
    return null
  }
}
```

**File:** app/src/lib/trampoline/trampoline-command.ts (L6-38)
```typescript
/** Represents a command in our trampoline mechanism. */
export interface ITrampolineCommand {
  /**
   * Identifier of the command.
   *
   * This will be used to find a suitable handler in the app to react to the
   * command.
   */
  readonly identifier: TrampolineCommandIdentifier

  /**
   * Trampoline token sent with this command via the DESKTOP_TRAMPOLINE_TOKEN
   * environment variable.
   */
  readonly trampolineToken: string

  /**
   * Parameters of the command.
   *
   * This corresponds to the command line arguments (argv) except the name of
   * the program (argv[0]).
   */
  readonly parameters: ReadonlyArray<string>

  /** Environment variables that were set when the command was invoked. */
  readonly environmentVariables: ReadonlyMap<string, string>

  /**
   * The standard input received by the trampoline (note that when running as
   * an askpass handler the trampoline won't read from stdin)
   **/
  readonly stdin: string
}
```
