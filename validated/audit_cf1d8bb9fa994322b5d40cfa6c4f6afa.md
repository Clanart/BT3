### Title
SSH askpass credential lookup/storage is keyed by attacker-controlled prompt text, not the actual remote host, enabling cross-host password replay - (File: app/src/lib/trampoline/trampoline-askpass-handler.ts)

### Summary
The original report is about a signature verification path that omits a domain separator, letting a valid proof from one context be replayed in another. The closest analog in GitHub Desktop is the SSH askpass flow: the "domain" that should scope a stored secret (the actual git remote host) is never independently established by Desktop. Instead, the key used to fetch/store the cached SSH password is parsed straight out of the `ssh` subprocess's stderr prompt text, which is fully controlled by whatever server the socket is connected to. A malicious or MITM'd SSH server can shape that prompt to impersonate a trusted host string and get Desktop's askpass trampoline to hand it a credential that was cached for a different, legitimate host.

### Finding Description
When `ssh` needs a password it invokes `SSH_ASKPASS` (Desktop's trampoline), passing the literal prompt text it wants displayed, e.g. `git@github.com's password: `. `createAskpassTrampolineHandler` dispatches this to `handleSSHUserPassword`: [1](#0-0) 

The `username` extracted here (`matches[1]`) is *whatever string appears before `'s password: ` in the prompt* — nothing ties it to the real remote host Desktop is talking to. That string is then used directly as the lookup key into the OS keychain via `getSSHUserPassword`: [2](#0-1) 

The storage function's own doc comment concedes the assumption is not enforced, only conventional: `username` is "**Usually** in the form of `user@hostname`" [3](#0-2) . There is no independent verification against the git remote URL Desktop actually initiated the connection to (comparable to how `findGitHubTrampolineAccount` checks `origin` for HTTPS credentials [4](#0-3) ). For SSH, no such origin binding exists — the "domain separator" that should scope the secret (the verified host) is missing, and the substitute value used instead (the prompt string) is attacker-influenced.

By contrast, the SSH host-key trust prompt at least hard-codes a fingerprint check for `github.com` before ever consulting user input [5](#0-4) , showing the project is aware that prompt-derived host data can't be blindly trusted — yet `handleSSHUserPassword` has no equivalent safeguard.

### Impact Explanation
If a user has previously stored an SSH password for `git@github.com` (or an enterprise host) and later clones/fetches from an attacker-controlled or MITM SSH endpoint, the attacker's SSH server can emit a password prompt whose textual username matches the trusted key (`git@github.com's password: `), even though the actual TCP/SSH session is with the attacker. Desktop's trampoline will look up and transmit the previously-stored password to that attacker-controlled server — an exfiltration of a real credential to an unauthorized destination, driven purely by attacker-supplied prompt content. This matches the "credential/token exfiltration via attacker-controlled remote/proxy response" impact class.

### Likelihood Explanation
Exploitation requires the user to add or fetch/push against a git remote whose SSH transport is attacker-controlled (a malicious remote URL, a compromised/MITM'd git-over-SSH server, or a proxied SSH connection) — squarely within scope since the attacker only needs to control the remote/server response, not the local machine. The prerequisite (a previously stored password for the impersonated `user@host` string) is a normal outcome of Desktop's own "remember password" feature, making the attack realistic for users who use SSH password auth (still supported per `handleSSHUserPassword`) with any credential caching enabled.

### Recommendation
Do not use the raw, server-supplied prompt string as the sole trust anchor for credential lookup/storage. Bind the SSH credential cache key to the git remote URL/host that Desktop itself resolved and initiated the connection to (the same way `findGitHubTrampolineAccount` binds by `origin`), and only fall back to (or corroborate with) the prompt-derived username after confirming it matches the expected host for the current trampoline/operation context (`trampolineEnvironmentPath`). This effectively adds the missing "domain separator" — the operation's real target host — to the credential-storage key.

### Proof of Concept
1. User stores an SSH password for `git@github.com` via Desktop's "remember password" prompt (normal usage).
2. Attacker sets up `evil-git-host` reachable via SSH and gets the user to add/fetch a remote pointing at it (e.g., a malicious "Open in Desktop" clone URL or a compromised proxy/redirect for an existing remote).
3. The `ssh` client connects to `evil-git-host`; the attacker's SSH server sends a password prompt with the literal text `git@github.com's password: ` (fully attacker-controlled banner/prompt content).
4. Desktop's trampoline askpass handler (`handleSSHUserPassword`) parses `username = "git@github.com"`, looks it up via `getSSHUserPassword`, finds the previously stored password, and returns it to the `ssh` process, which sends it to `evil-git-host`.
5. The attacker now possesses the user's real `github.com` SSH password, exfiltrated without any host/origin check.

### Citations

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L28-37)
```typescript
  // We'll accept github.com as valid host automatically. GitHub's public key
  // fingerprint can be obtained from
  // https://docs.github.com/en/github/authenticating-to-github/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
  if (
    info.host === 'github.com' &&
    info.keyType === 'RSA' &&
    info.fingerprint === 'SHA256:nThbg6kXUpJWGl7E1IGOCspRomTxdCARLviKw6E5SY8'
  ) {
    return 'yes'
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

**File:** app/src/lib/ssh/ssh-user-password.ts (L21-29)
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
