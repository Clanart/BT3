### Title
Non-canonical share path returns a blame proof revealing the ECDH key before the message's proof-of-possession is verified - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to "state changes before checks" (side effects ordered ahead of validation), `KeyMachine::calculate_share` decrypts each incoming `EncryptedMessage`, materializes the ECDH shared key into an `EncryptionKeyProof`, and returns it as blame when the decrypted share bytes are not a canonical scalar — all before the message's Schnorr proof-of-possession (PoP) has been verified, since PoP verification is deferred to a `BatchVerifier` that is only run at the end of the loop.

### Finding Description
In `crypto/dkg/pedpop/src/lib.rs`, `calculate_share` iterates over attacker-supplied `EncryptedMessage`s (fully untrusted bytes read via `EncryptedMessage::read`). For each message it calls `Encryption::decrypt` (`crypto/dkg/pedpop/src/encryption.rs:469-501`), which:

1. Queues the PoP for batch verification (`msg.pop.batch_verify`, line 479-485) — no verification yet.
2. Computes `key = ecdh(self.enc_key, msg.key)` and decrypts the ciphertext (lines 487-488).
3. Returns an `EncryptionKeyProof { key, dleq }` that publicly reveals the ECDH shared key.

Back in `calculate_share` (lib.rs:480-482), if the decrypted bytes fail `C::F::from_repr`, the function returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` immediately — `batch.verify_with_vartime_blame()` at line 493 is never reached, so a forged/missing PoP is never detected on this path. The processor (`processor/src/key_gen.rs:416-426`) forwards this blame proof to the coordinator for publication.

The PoP exists precisely to stop this: `encryption.rs:83-90` documents that without it, an attacker can reuse Alice's per-message key, and Bob's blame would reveal `bX`, decrypting Alice's message to Bob. The early return at lib.rs:480-482 reintroduces exactly that bypass — the blame proof (which reveals the shared key) is emitted on a code path where the PoP is never checked.

Attack: malicious participant Eve copies the `key` field from honest Alice's `EncryptedMessage` to victim Bob (the DKG assumes authenticated but not confidential channels, so Eve can observe it), sets the ciphertext to non-canonical bytes (`0xFF...` which fails `from_repr`), and attaches any invalid PoP. Bob computes the identical ECDH shared key as Alice's message, hits the canonical-scalar early-return, and publishes a valid `EncryptionKeyProof` for Alice's shared key. Anyone can then decrypt Alice's secret share to Bob.

### Impact Explanation
Public disclosure of an honest participant's secret share (`Alice → Bob`) to all observers of the blame process. This leaks DKG secret material — a share of Alice's polynomial evaluated at Bob's index — undermining the confidentiality the ECDH/PoP scheme is designed to provide, and weakening the threshold assumption if combined with other share disclosures.

### Likelihood Explanation
Medium. Requires a malicious DKG participant who can observe another participant's encrypted share message on the authenticated channel and submit a crafted `EncryptedMessage` — squarely within the reachable-input model for this DKG. It triggers deterministically, but each execution aborts the DKG and leaks only one share.

### Recommendation
Do not emit a blame proof before the PoP for that message has been verified. Either verify `msg.pop` synchronously before producing the `EncryptionKeyProof`, or treat non-canonical plaintext the same as a pending-verification failure: queue the canonicality outcome and only release `blame` after `batch.verify_with_vartime_blame()` confirms the PoP. If the PoP fails, return `blame: None` (as the `BatchId::Decryption` path already does at lib.rs:495).

### Proof of Concept
```rust
// Inside KeyMachine::calculate_share, for a malicious sender `eve`:
// Eve crafts EncryptedMessage { key: alice_msg.key (copied), pop: <invalid>, msg: 0xFF..FF }
let (share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(eve), eve, forged_msg);
// blame.key == ECDH(bob_enc_key, alice_msg.key) — the same shared key protecting
// Alice's secret share to Bob — produced despite the invalid PoP.
// share_bytes fails from_repr:
Option::<C::F>::from(C::F::from_repr(share_bytes.0)); // None
// Early return at lib.rs:480-482 skips batch.verify_with_vartime_blame() at :493,
// so the invalid PoP is never detected, and blame (revealing the shared key) is
// returned and published by processor/src/key_gen.rs.
```
Root cause: `Encryption::decrypt` performs the ECDH key-revealing work unconditionally while deferring the PoP check to a batch the early-return path never executes — a state-changing action ordered before its guarding check.