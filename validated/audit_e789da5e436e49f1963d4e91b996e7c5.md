### Title
Overwrite of per-operation SSH credential tracking map causes stale/wrong credentials to escape cleanup - ([File: app/src/lib/ssh/ssh-credential-storage.ts])

### Summary
`mostRecentSSHCredentials` is a `Map<string, SSHCredentialEntry>` keyed only by `operationGUID` (the trampoline token for the whole git operation), storing a single `{store, key}` pair. [1](#0-0)  Every time the askpass trampoline is invoked during that one git operation — for a host-key prompt, a key passphrase prompt, or a user-password prompt — the handler calls `setMostRecentSSHCredential(operationGUID, ...)`, which unconditionally overwrites the map entry for that `operationGUID`. [2](#0-1)  This is structurally identical to the reported bug class: a single mapping keyed by one identifier (there: `msg.sender`; here: `operationGUID`) that is meant to gate/undo a security-relevant action per-attempt, but silently loses track of prior attempts whenever a new one occurs.

### Finding Description
A single `git` invocation can legitimately trigger the askpass trampoline multiple times: SSH will try several identity files in sequence (multiple `Enter passphrase for key '...'` prompts, each for a different `keyPath`), and/or fall through to a username/password prompt after key-based auth fails. Each of these paths calls `setMostRecentSSHKeyPassphrase` / `setSSHKeyPassphrase` / `setSSHUserPassword` with the *same* `operationGUID`, and each of those calls funnels into `setMostRecentSSHCredential(operationGUID, store, key)`, which does `mostRecentSSHCredentials.set(operationGUID, { store, key })` — replacing whatever entry (if any) was recorded for an earlier prompt in the same operation. [3](#0-2) [4](#0-3) 

When the overall git operation ultimately fails with an SSH authentication error, `withTrampolineEnv` calls `deleteMostRecentSSHCredential(token)`, which only deletes the *single* `{store, key}` currently referenced by the map for that token — i.e. only the last-prompted credential. [5](#0-4) [6](#0-5)  Any earlier credential that was persisted to the OS keychain via `TokenStore.setItem` during the same operation (e.g. a wrong passphrase entered for the first identity file SSH tried) is never cleaned up, because its tracking entry was clobbered by the second/third prompt's entry before the failure occurred.

The comment in `ssh-credential-storage.ts` explicitly documents the intended one-shot semantics ("Keeps the SSH credential details in memory to be deleted later if the ongoing git operation fails to authenticate") without accounting for multiple prompts occurring within one `operationGUID`. [7](#0-6) 

An attacker who controls a remote/SSH endpoint the user is connecting to (e.g. a malicious `git+ssh` remote, or a cloned repo with a submodule URL pointing at an attacker-controlled SSH server) can cause the SSH client to solicit multiple credential prompts within a single Desktop-initiated git operation (e.g. offering multiple host keys/identities, or forcing keyboard-interactive fallback after rejecting an initial key). This lets the attacker manipulate which credential is "most recent" and therefore which one Desktop can still clean up, while a stale/incorrect passphrase that was actually typed by the user for a different (possibly attacker-influenced) key remains permanently stored in the OS credential store.

### Impact Explanation
Impact is credential persistence/corruption rather than direct code execution: a passphrase entered by the user for one SSH key can remain permanently cached in the OS keychain even though the overall operation failed and the corresponding cleanup path only targets the last-seen entry. This is analogous to the "Insurance timelock" report's core defect — a mapping overwrite silently defeats the intended per-action bookkeeping (there: block-number reset guard; here: SSH credential deletion-on-failure guard) — but here the corrupted value is security-sensitive stored credential state rather than a wait timer.

### Likelihood Explanation
Requires a git operation over SSH where the remote/attacker can force more than one askpass prompt in a single operation (multiple identity files, host-key re-prompt, then password fallback) — plausible whenever a user clones/fetches from or adds a remote controlled by an attacker, without any local access or malware assumption. The changelog shows this exact class of bug ("Wrong SSH key passphrases are not stored after multiple failed attempts and then one successful" #12804, "Wrong SSH key passphrases are not stored" #12800) has historically manifested in this subsystem. [8](#0-7) 

### Recommendation
Track pending SSH credentials per-attempt (e.g. keyed by `(operationGUID, store, key)` or in a list/set per `operationGUID`) instead of a single overwritable slot, so `deleteMostRecentSSHCredential` can clean up every credential that was tentatively stored during the failed operation, not just the last one.

### Proof of Concept
1. User adds/clones from a malicious SSH remote that is configured to reject the first offered identity (or first passphrase) and force a second prompt (e.g. a different identity file, or fallback to password auth).
2. During the single git operation (`operationGUID` = trampoline token), `handleSSHKeyPassphrase` is invoked twice for two different `keyPath`s; the user enters and chooses to store a passphrase both times. Each call to `setSSHKeyPassphrase` → `setSSHCredential` → `setMostRecentSSHCredential(operationGUID, ...)` overwrites the map entry for that token. [9](#0-8) 
3. The malicious server ultimately still causes an `SSHAuthenticationFailed`/`SSHPermissionDenied` error.
4. `withTrampolineEnv`'s catch block calls `deleteMostRecentSSHCredential(token)`, which deletes only the second key's stored passphrase from the keychain; the first key's incorrect/attacker-influenced passphrase remains persisted indefinitely. [5](#0-4)

### Citations

**File:** app/src/lib/ssh/ssh-credential-storage.ts (L9-23)
```typescript
type SSHCredentialEntry = {
  /** Store where this entry is stored. */
  store: string

  /** Key used to identify the credential in the store (e.g. username or hash). */
  key: string
}

/**
 * This map contains the SSH credentials that are pending to be stored. What this
 * means is that a git operation is currently in progress, and the user wanted
 * to store the passphrase for the SSH key, however we don't want to store it
 * until we know the git operation finished successfully.
 */
const mostRecentSSHCredentials = new Map<string, SSHCredentialEntry>()
```

**File:** app/src/lib/ssh/ssh-credential-storage.ts (L58-64)
```typescript
export function setMostRecentSSHCredential(
  operationGUID: string,
  store: string,
  key: string
) {
  mostRecentSSHCredentials.set(operationGUID, { store, key })
}
```

**File:** app/src/lib/ssh/ssh-credential-storage.ts (L78-87)
```typescript
export async function deleteMostRecentSSHCredential(operationGUID: string) {
  const entry = mostRecentSSHCredentials.get(operationGUID)
  if (entry) {
    log.info(
      `SSH auth failed, deleting credential for ${entry.store}:${entry.key}`
    )

    await TokenStore.deleteItem(entry.store, entry.key)
  }
}
```

**File:** app/src/lib/ssh/ssh-key-passphrase.ts (L37-54)
```typescript
export async function setSSHKeyPassphrase(
  operationGUID: string,
  keyPath: string,
  passphrase: string
) {
  try {
    const keyHash = await getHashForSSHKey(keyPath)

    await setSSHCredential(
      operationGUID,
      SSHKeyPassphraseTokenStoreKey,
      keyHash,
      passphrase
    )
  } catch (e) {
    log.error('Could not store passphrase for SSH key:', e)
  }
}
```

**File:** app/src/lib/ssh/ssh-key-passphrase.ts (L65-80)
```typescript
export async function setMostRecentSSHKeyPassphrase(
  operationGUID: string,
  keyPath: string
) {
  try {
    const keyHash = await getHashForSSHKey(keyPath)

    setMostRecentSSHCredential(
      operationGUID,
      SSHKeyPassphraseTokenStoreKey,
      keyHash
    )
  } catch (e) {
    log.error('Could not store passphrase for SSH key:', e)
  }
}
```

**File:** app/src/lib/ssh/ssh-user-password.ts (L43-61)
```typescript
/**
 * Keeps the SSH credential details in memory to be deleted later if the ongoing
 * git operation fails to authenticate.
 *
 * @param operationGUID A unique identifier for the ongoing git operation. In
 *                      practice, it will always be the trampoline secret for the
 *                      ongoing git operation.
 * @param username      SSH user name.
 */
export function setMostRecentSSHUserPassword(
  operationGUID: string,
  username: string
) {
  setMostRecentSSHCredential(
    operationGUID,
    SSHUserPasswordTokenStoreKey,
    username
  )
}
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L148-157)
```typescript
    } catch (e) {
      if (!getIsBackgroundTaskEnvironment(token)) {
        // If the operation fails with an SSHAuthenticationFailed error, we
        // assume that it's because the last credential we provided via the
        // askpass handler was rejected. That's not necessarily the case but for
        // practical purposes, it's as good as we can get with the information we
        // have. We're limited by the ASKPASS flow here.
        if (isSSHAuthFailure(e)) {
          deleteMostRecentSSHCredential(token)
        }
```

**File:** changelog.json (L2091-2097)
```json
    "2.9.1-beta7": [
      "[Fixed] Wrong SSH key passphrases are not stored after multiple failed attempts and then one successful - #12804"
    ],
    "2.9.1-beta6": [
      "[Fixed] Wrong SSH key passphrases are not stored - #12800",
      "[Fixed] Show SSH prompts (key passphrase, adding host, etc.) to macOS users via dialog - #12782"
    ],
```
