### Title
Unvalidated participant indexes in the PedPoP blame path cause a panic (DoS) on untrusted accusation input - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
The DKG blame-resolution functions `BlameMachine::blame` / `AdditionalBlameMachine::blame` take `sender` and `recipient` `Participant` values supplied by the accusing party, and index `self.enc_keys[&decryptor]` and `self.commitments[&sender]` without checking that those indexes are in range `1..=n`. Any nonzero `u16` is a valid `Participant`, so an accusation naming a recipient or sender outside the participant set panics the node, mirroring CVE-2020-19469's unprivileged-input-driven denial of service.

### Finding Description
`AdditionalBlameMachine::new` populates `Decryption.enc_keys` and `commitments` only for participants `1..=n` read from commitment messages. Later, `blame` → `blame_internal` calls `Decryption::decrypt_with_proof`, which does `self.enc_keys[&decryptor]` at `crypto/dkg/pedpop/src/encryption.rs:388`, and on a well-formed share evaluates `self.commitments[&sender]` at `crypto/dkg/pedpop/src/lib.rs:599`. Neither `blame` nor `blame_internal` validates `sender`/`recipient` against `n` — `Participant::new` only rejects `0`, so `Participant(0xffff)` or any index `> n` passes in cleanly. The result is a HashMap-missing-key panic inside library code reachable purely from bytes/values the accusing participant supplies (`sender`, `recipient`, the `EncryptedMessage`, and the optional `EncryptionKeyProof`, all read via `EncryptedMessage::read` / `EncryptionKeyProof::read`).

The same missing-entry panic exists at `encryption.rs:466` (`self.decryption.enc_keys[&participant]` in `Encryption::encrypt`) and at `pedpop/src/lib.rs:490` (`self.commitments[&l]` in `calculate_share`), though `calculate_share` is protected by `validate_map`. The blame path has no such guard because accusations are evaluated *after* `validate_map` has run and are attacker-initiated.

### Impact Explanation
A single participant (or, via `AdditionalBlameMachine`, any party evaluating blame over relayed accusations) can crash the DKG/blame-handling thread by issuing an accusation with an out-of-range `sender` or `recipient` index. This aborts key generation / blame adjudication — a denial of service reachable by an unprivileged party through public protocol inputs, with no key material needed. It matches the medium-severity crash class of the reference CVE (invalid memory access on attacker-influenced state).

### Likelihood Explanation
Blame is an expected protocol flow: honest nodes must accept accusations from other participants and evaluate them via `blame`. The accusation's `sender`/`recipient` fields are attacker-controlled and unconstrained by `ThresholdParams`; nothing in `Participant` bounds the index to `n`. Triggering requires only submitting an accusation naming a nonexistent index — trivial for any DKG participant or anyone able to relay an accusation to an `AdditionalBlameMachine`.

### Recommendation
Validate `sender` and `recipient` in `blame_internal` (and `AdditionalBlameMachine::blame`) against the registered participant set before indexing: return the accuser/accused as faulty or return a dedicated `PedPoPError::InvalidParticipant` instead of panicking. Similarly, replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `get(...).ok_or(...)` lookups.

### Proof of Concept
```rust
// Setup: run an n-party PedPoP DKG to completion, building
// AdditionalBlameMachine::new(context, n, commitment_msgs) — or a
// participant's BlameMachine — then evaluate an accusation where the
// recipient index exceeds n:
let bogus = Participant::new(n + 1).unwrap(); // valid Participant, unregistered
let msg = EncryptedMessage::<C, SecretShare<C::F>>::read(&mut arbitrary_bytes, params)?;
let proof = Some(EncryptionKeyProof::<C>::read(&mut proof_bytes)?);

// Panics at crypto/dkg/pedpop/src/encryption.rs:388:
//   self.enc_keys[&decryptor]  -> "key not found" panic
machine.blame(honest_sender, bogus, msg, proof);

// Variant: decrypt fails with None proof path skipped only if pop verifies;
// a bogus *sender* panics at pedpop/src/lib.rs:599:
//   self.commitments[&sender]
machine.blame(bogus, honest_recipient, valid_msg, valid_proof);
```

Relevant code: `crypto/dkg/pedpop/src/lib.rs` (`blame`/`blame_internal`, lines 575–609; `AdditionalBlameMachine::new`, lines 649–662) and `crypto/dkg/pedpop/src/encryption.rs` (`decrypt_with_proof`, line 388).