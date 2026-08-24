### Title
SSH askpass credential lookup keyed only by attacker-controllable username string enables password exfiltration to an unauthorized host - (File: app/src/lib/trampoline/trampoline-askpass-handler.ts)

### Summary
`handleSSHUserPassword` extracts a "username" purely from the text of an SSH prompt and uses that string as the lookup key for a previously stored SSH password, without verifying that the string actually corresponds to the host the current git operation is talking to. Because GitHub Desktop forces every SSH prompt (including server-driven keyboard-interactive prompts) through its askpass trampoline, a malicious or compromised SSH remote can synthesize a prompt string that matches a *different, previously-authorized* host's username and thereby trick Desktop into releasing the stored secret to it.

### Finding Description
Desktop always sets `SSH_ASKPASS`/`DISPLAY` so that OpenSSH routes *all* interactive prompts — not just literal password prompts, but also keyboard-interactive ones, whose prompt text is defined by the remote server — through Desktop's trampoline askpass program: [1](#0-0) 

`createAskpassTrampolineHandler` dispatches based purely on the shape of that prompt string: [2](#0-1) 

`handleSSHUserPassword` then extracts the "username" with a permissive regex and uses it directly as the storage key, with no binding to the actual remote/host of the current operation: [3](#0-2) 

```
const promptRegex = /^(.+@.+)'s password: $/
```

`getSSHUserPassword`/`setSSHUserPassword` treat this arbitrary string as an opaque `TokenStore` key with no host validation: [4](#0-3) [5](#0-4) 

Because a keyboard-interactive prompt's text is chosen by the remote SSH server, an attacker-controlled remote can present the literal string `"git@github.com's password: "` (or any other username/host combination the user has previously used with Desktop, such as an enterprise `git@<host>` account). The trampoline handler cannot distinguish this from a genuine prompt originating from the real host it corresponds to — the regex only checks shape, not provenance, and `operationGUID`/`trampolineToken` is never used to validate which remote the git process is actually connected to. If the user has previously stored a password under that exact username key, `getSSHUserPassword` returns it and it is sent by ssh, as the authentication response, to the attacker's server.

This contrasts with `handleSSHKeyPassphrase`, which mitigates the same class of issue by hashing the actual local key file content (`getFileHash`) rather than trusting the attacker-influenced key path as the lookup key directly — `handleSSHUserPassword` has no equivalent host/identity anchor.

### Impact Explanation
A stored SSH user password (a git credential Desktop holds in the OS keychain via `TokenStore`) can be exfiltrated to a host the user never intended to authenticate to, simply by cloning/fetching a repository (or submodule) whose remote points at an attacker-controlled SSH endpoint. This matches the "credential/token exfiltration to unauthorized host" impact category.

### Likelihood Explanation
Exploitation requires: (1) the user has, at some point, chosen to store an SSH password under a username string the attacker can predict/guess (well-known conventions like `git@<known-host>` make this plausible for enterprise Git hosts), and (2) the user's git operation connects to an attacker-controlled or attacker-redirected SSH remote (e.g., via a malicious submodule URL or a compromised/added remote). SSH password authentication for GitHub-hosted services is uncommon (GitHub.com SSH uses keys only), which reduces likelihood somewhat, but self-hosted/enterprise Git-over-SSH setups using password auth are realistic targets, and no code path validates that the prompt's implied host matches the operation's actual target host.

### Recommendation
Bind the stored SSH-password credential to the actual remote/host being operated on (e.g., derive/verify the host component of the prompt-supplied username against the git remote URL associated with `operationGUID`) rather than trusting the raw prompt text as the sole lookup key. Alternatively, hash or otherwise cryptographically anchor the credential key to verified connection metadata (similar to the file-hash approach used for SSH key passphrases), and refuse to answer keyboard-interactive/password prompts whose implied host does not match the operation's intended remote.

### Proof of Concept
1. User has previously used Desktop to SSH-authenticate with password to `git@enterprise-git.example.com` and chosen "remember password."
2. Attacker gets the user to add/fetch a remote pointing to `ssh://attacker.example.net` (e.g., via a malicious submodule URL in a cloned repository).
3. Attacker's SSH server offers keyboard-interactive auth and sends a prompt whose text is exactly `git@enterprise-git.example.com's password: `.
4. Because `SSH_ASKPASS`/`DISPLAY` are forced, OpenSSH invokes Desktop's trampoline askpass with that string as `firstParameter`.
5. `handleSSHUserPassword` matches the regex, extracts `git@enterprise-git.example.com`, and returns the stored password via `getSSHUserPassword`.
6. OpenSSH sends that password to the attacker's server as the authentication response, exfiltrating the credential to a host the user never authorized.

### Citations

**File:** app/src/lib/ssh/ssh.ts (L44-49)
```typescript
export async function getSSHEnvironment() {
  const baseEnv = {
    SSH_ASKPASS: getDesktopAskpassTrampolinePath(),
    // DISPLAY needs to be set to _something_ so ssh actually uses SSH_ASKPASS
    DISPLAY: '.',
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

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L166-168)
```typescript
    if (firstParameter.endsWith("'s password: ")) {
      return handleSSHUserPassword(command.trampolineToken, firstParameter)
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

**File:** app/src/lib/ssh/ssh-user-password.ts (L21-41)
```typescript
/**
 * Stores the SSH user password.
 *
 * @param operationGUID A unique identifier for the ongoing git operation. In
 *                      practice, it will always be the trampoline token for the
 *                      ongoing git operation.
 * @param username      SSH user name. Usually in the form of `user@hostname`.
 * @param password      Password for the given user.
 */
export async function setSSHUserPassword(
  operationGUID: string,
  username: string,
  password: string
) {
  await setSSHCredential(
    operationGUID,
    SSHUserPasswordTokenStoreKey,
    username,
    password
  )
}
```
