### Title
Panic in PedPoP blame decryption via missing registered encryption key (`decrypt_with_proof`) - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CVE-2025-20036 (untrusted message content causing a crash), `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` unconditionally. `enc_keys` is only populated via `Decryption::register`, which is fed by each participant's `EncryptionKeyMessage`. Nothing in `decrypt_with_proof` validates that `decryptor` actually registered a key, so an attacker-influenced blame flow against an unregistered/absent participant reaches a `HashMap` index panic instead of a `DecryptionError`.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`:

- `Decryption::register` is the only writer to `enc_keys`, and it is only invoked when a participant's `EncryptionKeyMessage` is supplied by the caller (`Encryption::register`, lines 351-362, 452-458).
- `decrypt_with_proof` first verifies the message PoP, then, when `proof: Some(EncryptionKeyProof)` is supplied, executes:
  ```rust
  &[self.enc_keys[&decryptor], *proof.key],
  ```
  at line 388. `HashMap`'s `Index` impl panics when the key is absent.
- Both the `EncryptedMessage` (read via `EncryptedMessage::read`, lines 171-177, which parses attacker-controlled bytes via `C::read_G`, `SchnorrSignature::read`, and `E::read`) and the `EncryptionKeyProof` (lines 267-269) are fully attacker-controlled on the wire. The PoP check only binds the sender `from`; it does not prove `decryptor` is registered.
- The same unchecked-index pattern exists in `Encryption::encrypt` at line 466 (`self.decryption.enc_keys[&participant]`), but `decrypt_with_proof` is the variant reachable during blame resolution, where the counterparty set is attacker-influenced.

A malicious or faulty participant can therefore reach the blame path (e.g., an accusation naming a `decryptor` who never delivered a valid `EncryptionKeyMessage`, or whose registration the application rejected/skipped) and cause a panic rather than a `DecryptionError::InvalidProof`.

### Impact Explanation
A panic in the DKG/blame handler aborts the key-generation (or key-rotation/recovery) session and, in typical processor usage, crashes the executing task/process. Since PedPoP is used to set up `ThresholdKeys` for Bitcoin multisigs in this codebase, a single malicious participant message can deny service to the entire validator set's key generation or to a blame-resolution pass — a remotely triggerable availability failure matching the CVE's "malicious post → crash" class.

### Likelihood Explanation
Reachability requires only that `decrypt_with_proof` be invoked with `proof = Some(..)` for a `decryptor` absent from `enc_keys`. Registration is caller-driven and per-participant; a participant who never registered (or whose registration message was dropped as invalid) can still be named in a blame flow, and the library performs no `contains_key` check before indexing. The panic is deterministic once reached — no cryptographic work is required of the attacker beyond participation in the protocol.

### Recommendation
Replace the unchecked index with a fallible lookup:

```rust
let Some(decryptor_key) = self.enc_keys.get(&decryptor) else {
  return Err(DecryptionError::InvalidProof);
};
```

and use `*decryptor_key` in the DLEq generators list. Apply the same treatment to `Encryption::encrypt` (line 466), which has the identical unchecked `enc_keys[&participant]` access, so encrypting to an unregistered participant returns an error instead of panicking.

### Proof of Concept
1. Instantiate `Encryption::<C>::new(context, i, rng)` for participant `i`.
2. Register encryption keys only for a strict subset of participants; leave participant `d` unregistered (or simulate their `EncryptionKeyMessage` being rejected).
3. Read an `EncryptedMessage<C, E>` via `EncryptedMessage::read` from attacker-controlled bytes with a valid PoP from `from`.
4. Call `into_decryption().decrypt_with_proof(from, d, msg, Some(proof))` where `proof` is any `EncryptionKeyProof` read via `EncryptionKeyProof::read`.
5. Execution reaches `self.enc_keys[&d]` at `crypto/dkg/pedpop/src/encryption.rs:388` and panics with `key not found` before `DecryptionError::InvalidProof` can be returned — even though the DLEq verification is what should gate the outcome.

Caveat: this finding assumes the integrator can feed a blame/decryption request for a participant whose registration was not completed, which is consistent with PedPoP's documented stance that message ordering/deduplication is the caller's responsibility (per the `Commitments` doc comment in `crypto/dkg/pedpop/src/lib.rs`). If every caller strictly refuses blame processing for unregistered parties, reachability drops, but the library itself does not enforce that invariant.