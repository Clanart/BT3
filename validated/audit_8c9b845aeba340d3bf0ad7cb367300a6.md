### Title
Panic on missing encryption key entry during PedPoP blame decryption - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::decrypt_with_proof` (and `Encryption::encrypt`) index `self.enc_keys` with the square-bracket `HashMap` index operator, which panics when the keyed `Participant` never registered an `EncryptionKeyMessage`. This is Serai's analog of the CVE-2018-20426 bug class: a missing/NULL entry dereferenced while handling attacker-influenced protocol state, crashing the node instead of returning an error.

### Finding Description
`Decryption` stores per-participant encryption public keys in `enc_keys: HashMap<Participant, C::G>` (`crypto/dkg/pedpop/src/encryption.rs:344`). Entries are only inserted by `register`, which also `assert!`s on re-registration (`encryption.rs:356-360`).

In `decrypt_with_proof`, the DLEq proof tying the per-message key to the recipient's registered encryption key is verified with:

```rust
proof.dleq.verify(
  &mut encryption_key_transcript(self.context),
  &[C::generator(), msg.key],
  &[self.enc_keys[&decryptor], *proof.key],
)
```
`encryption.rs:388` indexes `self.enc_keys[&decryptor]` directly. Likewise `Encryption::encrypt` does `self.decryption.enc_keys[&participant]` (`encryption.rs:466`). If the `decryptor`/`participant` has no entry — because a participant's `EncryptionKeyMessage` was never delivered, was rejected, or the protocol reached the blame path before that registration was recorded — the `HashMap` index panics. The function's own error type (`DecryptionError`) shows recoverable failure was intended for bad proofs and signatures, but the missing-key case bypasses it entirely with a process abort.

`EncryptedMessage::read` (`encryption.rs:171-177`) and `EncryptionKeyProof::read` (`encryption.rs:267-269`) accept fully untrusted bytes (`read_G`, `SchnorrSignature::read`, `DLEqProof::read`), so the message and proof themselves place no constraint on whether the map entry exists — the crash is driven purely by protocol state a peer can influence.

### Impact Explanation
An unprivileged DKG participant can force an honest node to panic (unwrap-equivalent of a NULL dereference) during the share/decrypt-with-proof blame flow by arranging for a decryption attempt against a participant with no registered encryption key. In Rust this aborts the thread (or process under `panic=abort`), taking down key generation / signing for that node — a remote, input-triggered denial of service of the validator, matching the High-severity crash class of the source advisory (rated Medium here since impact is availability only, no secret leakage).

### Likelihood Explanation
The panic requires only that `decrypt_with_proof` (or `encrypt`) be invoked for a `Participant` absent from `enc_keys`. `register` is the sole insertion point and nothing in `decrypt_with_proof` checks membership first (`contains_key` is only asserted inside `register` to block duplicates). Any ordering gap, withheld/malformed `EncryptionKeyMessage`, or early blame trigger exposes it. Caveat: whether upstream callers strictly guarantee all `n` registrations before this call could not be fully verified within the indexed code; the in-function contract itself provides no such guarantee.

### Recommendation
Replace the indexing operations with checked lookups:

```rust
let Some(decryptor_key) = self.enc_keys.get(&decryptor) else {
  return Err(DecryptionError::InvalidProof); // or a dedicated UnknownParticipant variant
};
```
and similarly in `Encryption::encrypt`. Consider converting the `register` re-registration `assert!` into a returned error as well, since a duplicated registration message is also peer-influenced input.

### Proof of Concept
```rust
// Setup: Decryption::new(context) with NO register() call for `decryptor`.
let decryption = Decryption::<Ristretto>::new(context);
let msg = EncryptedMessage::<Ristretto, Share>::read(&mut attacker_bytes, params)?;
let proof = EncryptionKeyProof::<Ristretto>::read(&mut proof_bytes)?;
// Attacker supplies a msg with a valid PoP for msg.key (easy: they made the key),
// so the early `pop.verify` check passes.
// `self.enc_keys[&decryptor]` on the empty map panics:
//   "index out of bounds"/HashMap index panic -> thread abort.
let _ = decryption.decrypt_with_proof(from, decryptor, msg, Some(proof));
```
The PoP check at `encryption.rs:374-379` does not gate the panic: an attacker who generated `msg.key` produces a valid `pop`, so execution reaches `self.enc_keys[&decryptor]` and panics before any `DecryptionError` can be returned.