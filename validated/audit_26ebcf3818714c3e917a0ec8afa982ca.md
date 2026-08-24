### Title
SSH askpass credential store keyed only by attacker-controlled prompt username, enabling cross-host credential disclosure - ([File: app/src/lib/trampoline/trampoline-askpass-handler.ts])

### Summary
The bug report's core invariant is "a public, attacker-influenceable entry point can be made to invoke logic keyed by data the caller controls, causing wrong credentials to be delivered." The closest real analog in GitHub Desktop is not a Solidity-style proxy shadow, but the SSH askpass trampoline handler, which extracts a `username` directly from the raw text of the SSH `*'s password:` prompt and uses that string — with no binding to which host/remote actually issued the prompt — as the lookup/storage key for a saved password in the OS credential store.

### Finding Description
When Desktop performs a Git operation over SSH, it sets `GIT_ASKPASS` to the trampoline binary so SSH prompts are routed to `createAskpassTrampolineHandler` in [1](#0-0) . For password prompts, `handleSSHUserPassword` matches the raw prompt string against `/^(.+@.+)'s password: $/` and uses the captured group as `username` verbatim: [2](#0-1) .

That `username` is passed straight into `getSSHUserPassword`/`setSSHUserPassword`, which key the OS-level `TokenStore` purely on this string — there is no host, fingerprint, or remote-URL binding involved: [3](#0-2) .

The prompt text itself is entirely produced by the remote `ssh`/server side of the connection (it's OpenSSH's password prompt, printed to the askpass channel), not by Desktop or the local user. Since the lookup key is only the attacker-controlled string that appears before `'s password: `, any SSH server Desktop connects to (e.g., a remote configured in the repository, or a URL supplied via clone/fetch/add-remote) can print a prompt of the form `<victim-user>@<victim-host>'s password: ` where `<victim-user>@<victim-host>` matches a string previously used — and thus already stored — for a legitimate SSH remote. Desktop's trampoline will find the stored password via `getSSHUserPassword(username)` and hand it back as the answer to the prompt, i.e., send it to the connected SSH session, which is under the attacker's control.

The `Dispatcher`/`AppStore` proxy-shadow issue in the report and this Desktop bug share the same root cause pattern: a shared/underspecified identifier (`owner()` method name in the report; the raw prompt-derived `username` string here) is used to route to sensitive stored data without verifying that the caller is actually the entity the data was originally associated with.

### Impact Explanation
If a user has ever stored an SSH password for `user@host` via Desktop's askpass flow (`storeSecret` in `promptSSHUserPassword`), any subsequent SSH connection — including one to a completely different, attacker-controlled remote — that emits a prompt textually matching that same `user@host` string will receive that stored password automatically, without any user interaction. This is a credential-exfiltration primitive: the attacker who controls the remote (a malicious clone URL, or a compromised/MITM'd proxy in front of an SSH remote) can retrieve secrets Desktop stored for a different, legitimate host, purely by choosing a matching prompt string.

### Likelihood Explanation
Exploitation requires only that the victim has previously saved an SSH password through Desktop for some `user@host`, and later performs any git operation (clone/fetch/pull/push) against a remote whose SSH server chooses to print a spoofed prompt using that same string. Because the key is a raw substring of remote-controlled prompt text with no cryptographic or network-origin binding (unlike the SSH host-key fingerprint check used for `handleSSHHostAuthenticity`), the guard that exists for host-authenticity prompts (`handleSSHHostAuthenticity` checks fingerprint) is absent here, and does not stop this path — `handleSSHUserPassword` performs no equivalent verification before looking up/returning the cached secret.

### Recommendation
Bind the SSH user-password cache key to a verified identifier of the operation's actual remote (e.g., the trampoline token's associated repository/remote URL or resolved host, similar to how `getCredentialUrl`/`getHasRejectedCredentialsForEndpoint` scope other credential logic to an endpoint) rather than trusting the raw `user@host` substring parsed out of the SSH prompt text. At minimum, cross-check the parsed host portion against the actual remote host being contacted for the current `operationGUID` before returning a cached secret.

### Proof of Concept
1. User adds/clones a legitimate SSH remote `git@good-host.example`, is prompted for a password, and chooses "remember" — Desktop stores it via `setSSHUserPassword` keyed as `"git@good-host.example"`.
2. User later clones or adds a different repository whose remote is controlled by an attacker (e.g., `ssh://attacker.example/repo.git`).
3. The attacker's SSH server, instead of a normal auth prompt, prints `git@good-host.example's password: ` over the askpass channel (the server fully controls what it sends before authentication).
4. `handleSSHUserPassword` matches the regex, extracts `username = "git@good-host.example"`, calls `getSSHUserPassword("git@good-host.example")`, finds the previously stored password, and returns it as the answer — sending the victim's `good-host.example` credential to the attacker's SSH session at `attacker.example`. [2](#0-1) [4](#0-3)

### Citations

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

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L148-171)
```typescript
export const createAskpassTrampolineHandler: (
  accountsStore: AccountsStore
) => TrampolineCommandHandler =
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

    return undefined
  }
```

**File:** app/src/lib/ssh/ssh-user-password.ts (L11-41)
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
