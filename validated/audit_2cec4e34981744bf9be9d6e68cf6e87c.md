### Title
SSH key passphrase/user password is persisted to the OS credential store before the git operation's success is confirmed, and the failure-path cleanup races the un-awaited write - (File: `app/src/lib/trampoline/trampoline-askpass-handler.ts`)

### Summary
The Solidity report's broken invariant is: a balance/ledger value is mutated to reflect an operation as if it already completed successfully, before the actual external effect (ETH transfer) is confirmed, and the "undo" path can be bypassed by re-entering another function. The GitHub Desktop analog is the SSH askpass trampoline's handling of "remember this credential": it writes the secret to the OS keychain immediately, based only on the assumption that the in-flight git operation (fully controlled, on the wire, by the remote/attacker) will succeed - and the compensating delete-on-failure logic races against that same un-awaited write.

### Finding Description
When `ssh`/git prompts for a key passphrase or user password (triggered by the remote server during authentication), `handleSSHKeyPassphrase` and `handleSSHUserPassword` call `setSSHKeyPassphrase(...)` / `setSSHUserPassword(...)` **without awaiting them**: [1](#0-0) 

The comment even documents the intended (but unenforced) invariant: *"we'll keep it around to store it later if the git operation succeeds"* - yet the code stores it immediately instead of deferring the actual write: [2](#0-1) 

`setSSHCredential` records the pending entry in memory synchronously and then performs the actual (async) OS keychain write via `keytar`: [3](#0-2) 

The only rollback mechanism is `withTrampolineEnv`'s `catch`/`finally`, which deletes the "most recent" credential **only** when the failure is classified as an SSH auth failure, and unconditionally clears the in-memory pointer in `finally`: [4](#0-3) 

Because the write in `setSSHCredential`/`TokenStore.setItem` (line ~44 of `ssh-credential-storage.ts`) is fired-and-forgotten from the askpass handler, and the delete happens as soon as the git subprocess exits/throws, a remote endpoint that controls exactly when/how the SSH handshake fails (e.g., immediately reject the connection right after requesting - and receiving - the passphrase) can race the asynchronous `keytar.setPassword` write against the asynchronous `keytar.deletePassword` cleanup. If the write resolves after the delete has already run, the passphrase persists in the OS keychain even though the operation the user "accepted only if it succeeds" never actually succeeded, permanently breaking the "only store on success" invariant, similar to how the `idleETH`/reward-ledger update happened before confirming the ETH transfer actually completed in the Solidity finding.

### Impact Explanation
The corrupted value is the OS keychain entry keyed by `SSHKeyPassphraseTokenStoreKey`/`SSHUserPasswordTokenStoreKey` (see `getSSHCredentialStoreKey` in `app/src/lib/ssh/ssh-credential-storage.ts`), which should only exist when the user explicitly authorized "remember credential" *and* the underlying git operation genuinely succeeded. A remote/proxy the user connects to (a git remote is, per the assessment's threat model, attacker-controlled) can influence the timing/outcome of authentication and thereby cause sensitive local secret material (an SSH key passphrase or SSH account password) to be silently and durably persisted against the developer's stated intent, independent of the operation's actual outcome. This is a credential-handling integrity failure directly reachable from an untrusted git remote, without any local/physical access or prior compromise, matching the "credential/token exfiltration"-adjacent category (unauthorized persistence of decrypted-key material) called out as in-scope.

### Likelihood Explanation
The unawaited call is directly visible in the code path (`setSSHKeyPassphrase(...)` / `setSSHUserPassword(...)` invoked without `await` inside an `async` handler), so no timing "miracle" is required to demonstrate the missing sequencing - only precise control of when the remote SSH connection fails is needed to reliably win the race, which is entirely under the control of a malicious/compromised git server the user is fetching/pushing to.

### Recommendation
Await the credential-store write before allowing `handleSSHKeyPassphrase`/`handleSSHUserPassword` to return the secret to the askpass caller, or better, defer the actual `TokenStore.setItem` call until `withTrampolineEnv`'s `fn()` has resolved successfully (mirroring the mitigation pattern in the report: perform the state mutation *after* the operation is confirmed successful, not before). Concretely:
- In `ssh-credential-storage.ts`, split "record intent to store" (already done via `setMostRecentSSHCredential`) from "commit to keychain", and only call `TokenStore.setItem` from `withTrampolineEnv`'s success path (after `fn()` resolves) instead of from the askpass handler itself.
- Ensure the failure-path `deleteMostRecentSSHCredential` cannot race an in-flight commit by making the whole sequence (`git op result` → `commit or discard credential`) a single awaited chain rather than two independently-scheduled async operations.

### Proof of Concept
1. Add an SSH remote pointing to an attacker-controlled SSH server.
2. Trigger a fetch/push; the server prompts for the (real, local) SSH key's passphrase via the normal OpenSSH pubkey-auth flow, which invokes `handleSSHKeyPassphrase` in `app/src/lib/trampoline/trampoline-askpass-handler.ts`.
3. User agrees to "remember passphrase" → `setSSHKeyPassphrase(operationGUID, keyPath, passphrase)` is called but not awaited (line 103).
4. The attacker's server immediately terminates/rejects the SSH session right after receiving the (now-decrypted, but never transmitted) key material, causing `withTrampolineEnv`'s git subprocess call to throw an `SSHAuthenticationFailed`/`SSHPermissionDenied` error quickly.
5. The `catch` block calls `deleteMostRecentSSHCredential(token)` (`app/src/lib/trampoline/trampoline-environment.ts` line ~156), which races the still-pending `keytar.setPassword` call scheduled in step 3.
6. Depending on scheduling, the delete can complete before the write lands, leaving the passphrase permanently stored in the OS keychain despite the git operation having failed - violating the documented "store only if the operation succeeds" contract.

Note: I was unable to execute this PoC in a live environment (no filesystem/terminal access here); this is derived purely from static code-path analysis of the cited files. A background Devin session with terminal access would be needed to instrument `keytar` calls and empirically confirm the race window.

### Citations

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L92-108)
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
```

**File:** app/src/lib/ssh/ssh-credential-storage.ts (L37-45)
```typescript
export async function setSSHCredential(
  operationGUID: string,
  store: string,
  key: string,
  password: string
) {
  setMostRecentSSHCredential(operationGUID, store, key)
  await TokenStore.setItem(store, key, password)
}
```

**File:** app/src/lib/trampoline/trampoline-environment.ts (L148-200)
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
      }

      // Prior to us introducing the credential helper trampoline, our askpass
      // trampoline would return an empty string as the username and password if
      // we were unable to find an account or acquire credentials from the user.
      // Git would take that to mean that the literal username and password were
      // an empty string and would attempt to authenticate with those. This
      // would fail and Git would then exit with an authentication error which
      // would bubble up to the user. Now that we're using the credential helper
      // Git knows that we failed to provide credentials and instead of trying
      // to authenticate with an empty string it will exit with an error saying
      // that it couldn't read the username since terminal prompts were
      // disabled.
      //
      // We catch that specific error here and throw the user-friendly
      // authentication failed error that we've always done in the past.
      if (
        hasRejectedCredentialsForEndpoint.has(token) &&
        e instanceof GitError &&
        fatalPromptsDisabledRe.test(e.message)
      ) {
        const msg = 'Authentication failed: user cancelled authentication'
        const gitErrorDescription =
          getDescriptionForError(DugiteError.HTTPSAuthenticationFailed, '') ??
          msg

        const fakeAuthError = new GitError(
          { ...e.result, gitErrorDescription },
          e.args,
          msg
        )

        fakeAuthError.cause = e
        throw fakeAuthError
      }

      throw e
    } finally {
      removeMostRecentSSHCredential(token)
      isBackgroundTaskEnvironment.delete(token)
      hasRejectedCredentialsForEndpoint.delete(token)
      trampolineEnvironmentPath.delete(token)
    }
```
