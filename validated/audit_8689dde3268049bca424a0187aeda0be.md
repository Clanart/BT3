### Title
Unvalidated participant index panics blame evaluation (DoS) via HashMap indexing on untrusted accusation - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The ntpd-rs bug class is "attacker-supplied value used to index without bounds validation, panicking the process" (CWE-130). In Serai's PedPoP DKG, the blame path performs exactly this: `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` (crypto/dkg/pedpop/src/encryption.rs:388) and `BlameMachine::blame_internal` indexes `self.commitments[&sender]` (crypto/dkg/pedpop/src/lib.rs:599) with `Participant` values that are never checked against the DKG's participant set. `Participant::new` only rejects 0 (any `u16` up to 65535 is accepted), while `enc_keys`/`commitments` only ever contain entries for `1 ..= n`. An accusation naming an out-of-range `sender`/`recipient` panics the process evaluating blame.

### Finding Description
`AdditionalBlameMachine::new` (crypto/dkg/pedpop/src/lib.rs:649) builds `commitments` and `Decryption::enc_keys` strictly for `i in 1 ..= n`. Its public `blame` method, and `BlameMachine::blame`, accept `sender: Participant` and `recipient: Participant` from the caller — i.e., fields of an accusation another party published. `blame_internal` calls `decrypt_with_proof`, which evaluates `self.enc_keys[&decryptor]` as an argument to `DLEqProof::verify` (encryption.rs:388); a missing key panics via `Index` on `HashMap`. Even if `decrypt_with_proof` returned early, `blame_internal` later does `self.commitments[&sender]` (lib.rs:599) for any sender ∉ `{1..=n}`. Neither `sender` nor `recipient` is range-checked anywhere on this path.

This mirrors the advisory's root cause: attacker-reachable data used for indexing instead of a checked lookup, turning an expected error into a crash.

### Impact Explanation
An unprivileged party can crash any node evaluating their accusation (or an accusation they trigger). In Serai, blame evaluation is how the DKG aborts malicious dealers; a panic aborts key generation and prevents `BlameMachine::complete`/`AdditionalBlameMachine` from producing blame. A faulty DKG participant who sent an invalid share can additionally publish an accusation with `recipient = Participant(n+1)` (or a forged sender index > n), crashing honest participants' blame flow and stalling the multisig setup. `EncryptedMessage::read` and the message bytes themselves are untrusted inputs on this path.

### Likelihood Explanation
Requires only that a party submits a blame accusation with an out-of-range participant index — no collusion, no valid keys needed beyond participating in (or observing) the DKG. Any caller invoking `blame`/`blame_internal` with accusation-supplied `Participant` values hits the panic deterministically.

### Recommendation
Replace indexing with checked lookups:
- `self.enc_keys.get(&decryptor)` in `decrypt_with_proof` (encryption.rs:388) — return `DecryptionError::InvalidProof`/`sender` fault on `None`.
- `self.commitments.get(&sender)` in `blame_internal` (lib.rs:599) — treat missing sender as `sender` fault or an explicit error.
- Validate `sender`/`recipient` against `all_participant_indexes` (or `≤ n`) at the top of `BlameMachine::blame` and `AdditionalBlameMachine::blame`, mirroring `validate_map`.

### Proof of Concept
```rust
// Conceptual: a DKG with n = 3 participants completes commitments.
// Machine: AdditionalBlameMachine::new(context, 3, commitment_msgs)
// enc_keys and commitments now contain keys {1, 2, 3} only.

// Attacker-controlled accusation names a non-existent participant.
let attacker_recipient = Participant::new(4).unwrap(); // only 0 is rejected
let msg: EncryptedMessage<C, SecretShare<C::F>> =
  EncryptedMessage::read(&mut attacker_bytes, params).unwrap();

// Panics inside decrypt_with_proof at `self.enc_keys[&decryptor]`
// (HashMap Index impl -> "no entry found for key")
machine.blame(sender, attacker_recipient, msg, None);

// Symmetrically, a valid `msg` from a real sender but accusation naming
// sender = Participant::new(9) survives decrypt_with_proof and then
// panics at `self.commitments[&sender]` in blame_internal (lib.rs:599).
```
Uncertain aspects (index-size limits): I could not fully confirm whether `networks/bitcoin/src/wallet` `read` paths contain an even more direct short-buffer slicing panic, but the above is concretely supported by the cited code.