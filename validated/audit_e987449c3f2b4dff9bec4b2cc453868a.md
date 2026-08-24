### Title
SSH askpass "pending credential" state is keyed only by the trampoline token, letting one operation's credential entry clobber another's before it can be persisted or purged - ([File: app/src/lib/ssh/ssh-credential-storage.ts])

### Summary
`mostRecentSSHCredentials` tracks at most **one** "pending" SSH credential per `operationGUID` (the trampoline token for the whole git operation), even though a single git operation (clone/fetch/pull with submodules, or multiple remotes) can trigger the SSH askpass handler multiple times for *different* keys/hosts while reusing the same token. The last `setMostRecentSSHCredential`/`removeMostRecentSSHCredential` call for that token silently overwrites or drops the bookkeeping for any earlier credential in the same operation, so the cleanup/erase step at the end of the operation acts on the wrong (or no) credential — mirroring the `HATArbitrator` bug where a resolved/consumed bond record is not properly scoped, letting later action corrupt/consume the wrong record.

### Finding Description
`mostRecentSSHCredentials` is a `Map<string, SSHCredentialEntry>` keyed solely by `operationGUID`, which is "in practice... always the trampoline token for the ongoing git operation" [1](#0-0) . Because the map holds a single entry per token, calling `setMostRecentSSHCredential`/`setSSHCredential` a second time for the same token silently replaces the first entry: [2](#0-1) 

`trampoline-askpass-handler.ts` shows that a single operation (one `trampolineToken`) can invoke `handleSSHKeyPassphrase` / `handleSSHUserPassword` repeatedly — the code comment even acknowledges "it's possible that the user will need to enter the passphrase multiple times if there are failed attempts" and calls `removeMostRecentSSHCredential(operationGUID)` unconditionally by token, not by which specific key/host it corresponds to: [3](#0-2) 

The same pattern repeats for user/password prompts: [4](#0-3) 

`deleteMostRecentSSHCredential` (called on auth failure) also only looks up by `operationGUID` and purges whatever is currently in that single slot: [5](#0-4) 

This is the same broken invariant as the HATs report: a shared accounting slot (there: `disputersBonds`/`totalBondsOnClaim` per vault; here: one pending-credential slot per trampoline token) is not scoped to the specific resource it should represent (there: the specific claim; here: the specific SSH key/host), so a second legitimate event sharing the same key can clobber the bookkeeping for the first, causing incorrect cleanup instead of a clean failure.

### Impact Explanation
A single git operation that reaches multiple SSH endpoints under one trampoline token (e.g. a clone/pull that recurses into submodules, or a host that first offers a wrong key then falls back, common when an attacker controls a submodule URL/remote pointing at a different SSH host or forces multiple auth prompts) can:
- Cause the passphrase/password for the *first* successfully-authenticated key to never be deleted from `deleteMostRecentSSHCredential`'s intended path (because the slot now points at the second credential), leaving a credential in the OS keychain (`TokenStore`) that the user never explicitly asked to persist, or
- Cause `deleteMostRecentSSHCredential` to purge the *wrong* credential when a later prompt in the same operation fails, silently discarding a passphrase the user legitimately chose to remember.

This is triggerable from an untrusted git object the user clones/fetches (a repository with a malicious `.gitmodules`/submodule remote pointing at attacker-controlled SSH hosts), matching the "attacker controls a cloned/fetched repository ... remote" threat model, and results in silent, unintended persistence or loss of credential material in the local secret store.

### Likelihood Explanation
Requires the victim to clone/pull a repository (or add a remote/submodule) that is set up to prompt for SSH auth against more than one host/key within the same top-level git invocation — a normal, unprompted user action (clone/fetch), not requiring local access, admin rights, or social engineering beyond the standard "clone this repo" interaction that GitHub Desktop is built around.

### Recommendation
Key `mostRecentSSHCredentials` (and the associated set/remove/delete calls) by both `operationGUID` and the specific credential identity (store + key, or key path/username) rather than by `operationGUID` alone, e.g. use a `Map<string, SSHCredentialEntry>` keyed by `` `${operationGUID}:${store}:${key}` `` or a `Map<string, Map<string, SSHCredentialEntry>>` so each pending credential within an operation is tracked and cleaned up independently.

### Proof of Concept
1. Set up a repository with a `.gitmodules` referencing two different SSH remotes/hosts requiring separate keys/passwords (or a single host that first offers a key that fails, then requests a password).
2. Clone/pull that repository in GitHub Desktop; both askpass prompts occur under the same `trampolineToken` (`operationGUID`).
3. For the first prompt, choose to remember the credential (`setSSHKeyPassphrase` → `mostRecentSSHCredentials.set(token, A)`).
4. For the second prompt, decline to remember it, or have it fail (`removeMostRecentSSHCredential(token)`/`deleteMostRecentSSHCredential(token)`) — this deletes/ignores entry `A`, not `B`, because the map only tracks the last write for that token.
5. Observe (via `TokenStore`/keychain inspection) that credential `A`, which should have been purged on overall failure or was never meant to be permanently kept beyond user intent, remains in the OS keychain, or that a credential the user asked to keep is unexpectedly removed.

### Citations

**File:** app/src/lib/ssh/ssh-credential-storage.ts (L17-23)
```typescript
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

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L92-109)
```typescript
  const { secret: passphrase, storeSecret: storePassphrase } =
    await trampolineUIHelper.promptSSHKeyPassphrase(keyPath)

  // If the user wanted us to remember the passphrase, we'll keep it around to
  // store it later if the git operation succeeds.
  // However, when running a git command, it's possible that the user will need
  // to enter the passphrase multiple times if there are failed attempts.
  // Because of that, we need to remove any pending passphrases to be stored
  // when, in one of those multiple attempts, the user chooses NOT to remember
  // the passphrase.
  if (passphrase !== undefined && storePassphrase) {
    setSSHKeyPassphrase(operationGUID, keyPath, passphrase)
  } else {
    removeMostRecentSSHCredential(operationGUID)
  }

  return passphrase ?? ''
}
```

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L121-146)
```typescript
  const storedPassword = await getSSHUserPassword(username)
  if (storedPassword !== null) {
    // Keep this stored password around in case it's not valid and we need to
    // delete it if the git operation fails to authenticate.
    setMostRecentSSHUserPassword(operationGUID, username)
    return storedPassword
  }

  if (getIsBackgroundTaskEnvironment(operationGUID)) {
    log.debug(
      'handleSSHUserPassword: background task environment, skipping prompt'
    )
    return undefined
  }

  const { secret: password, storeSecret: storePassword } =
    await trampolineUIHelper.promptSSHUserPassword(username)

  if (password !== undefined && storePassword) {
    setSSHUserPassword(operationGUID, username, password)
  } else {
    removeMostRecentSSHCredential(operationGUID)
  }

  return password ?? ''
}
```
