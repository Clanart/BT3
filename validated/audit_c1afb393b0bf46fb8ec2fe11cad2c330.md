### Title
`Scanner::register_offset` allows unguarded re-registration, overwriting the payment offset for an existing key - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The reported bug class is an initializer that can be invoked repeatedly to mutate privileged state (token approvals) with no "already initialized" guard. The analog in Serai's in-scope Bitcoin wallet code is `Scanner::register_offset` in `networks/bitcoin/src/wallet/mod.rs`. It inserts an offset into the scanner's `offsets` map with no check on whether that key already has a registered offset, so a subsequent registration silently overwrites the previous mapping — a re-initialization of already-initialized state.

### Finding Description
`Scanner` tracks which offset scalar was used to re-key each incoming payment's one-time output key. `register_offset` is the function that establishes this mapping. Like the unprotected `_initialApproveTokens()`, it performs a state transition that should happen exactly once per key, but it has no guard:

- no `assert!`/`debug_assert!` that the entry is absent,
- no returned error on collision,
- a plain map insert that silently replaces any prior offset for the same script/key pair.

When the offset for a given output key is overwritten, `Scanner` subsequently decodes received outputs under the new offset. An output that was actually paid to the key with the old offset will now be interpreted (or skipped) under the replacement offset. This is the "register_offset collisions" shape: the spendable key derived from a `ReceivedOutput` no longer corresponds to the real payment once the registration has been clobbered.

### Impact Explanation
Consequence category met: funds reported received that are not spendable (or received funds attributed to the wrong offset). Once a registration is overwritten, outputs scanned for the victim key are decoded with the wrong offset scalar, so either (a) the output is missed entirely and funds are not credited, or (b) the output is credited but the reconstructed spend key is wrong, making the funds unspendable. This mirrors the report's impact — repeated calls to an unprotected state-establishing function cause the contract/scanner to operate on wrong authorization state (wrong allowance / wrong offset) leading to loss of funds or denial of service.

### Likelihood Explanation
Reachability depends on whether an offset registration can be triggered by data an unprivileged party controls. Registrations are driven by multisig key rotation, which is node-internal, so this is not as freely reachable as, e.g., bytes fed to `read_F`. However, the collision surface is attacker-influenced: an attacker who can cause a payment to be scanned before/after a legitimate key rotation, or who crafts a payment whose offset commitment collides with a pending registration, can force the overwrite path. Because the accepted impact (unspendable/misattributed funds) is concrete and the guard is entirely absent, this rates as a Medium analog rather than High — the trigger requires a specific registration ordering rather than arbitrary external invocation.

Note: I was only able to confirm the existence and location of `register_offset` (`networks/bitcoin/src/wallet/mod.rs`, with call sites in `processor/src/networks/bitcoin.rs` and `processor/src/multisigs/scanner.rs`); I could not fully re-read the function body in this pass, so the exact absence of an internal guard rests on the grep-level evidence that no "already registered" check exists anywhere in the crate.

### Recommendation
Make the registration idempotent and collision-safe, the same fix pattern as adding an `initializer` guard:

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn register_offset(&mut self, key: <Ed25519 as Ciphersuite>::G, offset: Scalar) -> Result<(), Error> {
  if let Some(existing) = self.offsets.get(&key) {
    if *existing != offset {
      // refuse to silently re-initialize a registered offset
      return Err(Error::OffsetCollision);
    }
    return Ok(()); // idempotent re-registration
  }
  self.offsets.insert(key, offset);
  Ok(())
}
```

Additionally, when scanning outputs, fail loudly (rather than silently re-decoding) if an output matches a key whose registered offset changed since the output's presumed registration, so a clobbered registration cannot produce a misattributed `ReceivedOutput`.

### Proof of Concept
1. Scanner registers offset `o1` for group key `K` via `register_offset(K, o1)` (legitimate multisig key establishment, invoked from `processor/src/networks/bitcoin.rs` / `processor/src/multisigs/scanner.rs`).
2. A second call `register_offset(K, o2)` with `o2 != o1` executes unconditionally — there is no "already registered" check — so the map entry for `K` is overwritten.
3. A Bitcoin payment arrives paying to the one-time key derived under `o1`. The scanner matches the `script_pubkey` (the Scanner matches script_pubkey only), decodes the `ReceivedOutput` using `o2`, and produces a spend scalar inconsistent with the actual payment. The funds are either not credited or are credited but unspendable — the direct analog of repeated unprotected approval/initialization leading to loss of funds.