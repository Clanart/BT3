### Title
Attacker-controlled participant indexes cause panics in PedPoP blame evaluation - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP blame API (`BlameMachine::blame` / `AdditionalBlameMachine::blame`) feeds attacker-supplied `sender` / `recipient` `Participant` values and an attacker-deserialized `EncryptedMessage` into `blame_internal`, which indexes `self.commitments[&sender]` and `self.enc_keys[&decryptor]` with no bounds check. A `Participant` can be constructed for any non-zero `u16`, so any index `> n` or absent from the maps triggers a panic, crashing the evaluator mid-blame-protocol.

### Finding Description
`EncryptedMessage::read` accepts arbitrary bytes for the message key and ciphertext (`crypto/dkg/pedpop/src/encryption.rs:171-177`). `blame_internal` then (a) verifies the PoP via `msg.pop.verify`, and (b) dereferences `self.enc_keys[&decryptor]` inside `decrypt_with_proof` (`crypto/dkg/pedpop/src/encryption.rs:388`) and `self.commitments[&sender]` at `crypto/dkg/pedpop/src/lib.rs:599`. Neither `sender` nor `recipient` is validated against `params.n()` or against membership in `commitments`/`enc_keys` before the `HashMap` index operator, which panics on a missing key. `AdditionalBlameMachine::new` builds `enc_keys`/`commitments` only for `i in 1 ..= n` (`crypto/dkg/pedpop/src/lib.rs:656-660`), so any `Participant` outside that range is guaranteed absent. The constructor docs acknowledge this class: "Usage of invalid commitments is considered undefined behavior, and may cause everything from inaccurate blame to panics" (`lib.rs:646-648`) — but the panic here arises purely from the `sender`/`recipient` arguments and message bytes, which in deployment are remote-supplied.

Additionally, `SchnorrSignature::read` (`crypto/schnorr/src/lib.rs:51-53`) accepts `R = identity` (only the FROST `Curve::read_G` wrapper rejects identity, `crypto/frost/src/curve/mod.rs:125-131`), so a crafted `msg.key`/PoP pair passes `pop.verify` trivially for `key = identity` (any `s` with `R = s·G` satisfies `s·G = R + c·0`), meaning the ECDH path at `encryption.rs:487-488` can be driven with an identity shared point. That path itself only yields an invalid share, but combined with the indexing panic the deserialization surface is reachable end-to-end.

### Impact Explanation
An unprivileged counterparty who can cause a victim to evaluate blame (e.g., by sending a malformed share and then a `blame`/VerifyBlame request naming an out-of-range `sender` or `recipient`) crashes the victim's key-gen/blame evaluation. In a validator/processor setting this is a remotely triggerable panic during DKG fault handling — precisely the moment the protocol is already under attack — halting the DKG and preventing completion or correct fault attribution. Severity: Medium (availability impact on a single node, no secret leakage).

### Likelihood Explanation
Triggering requires only that the attacker (or a message routed through an accuser) supplies `blame(...)`/`decrypt_with_proof` with a `Participant` index not registered in `1 ..= n` — e.g., `Participant::new(n + 1)`, or a valid nonzero index whose `EncryptionKeyMessage` was never registered. Both `blame` entry points accept these arguments unconditionally; `Participant::new` bounds only to non-zero `u16`, not to `n`. No collusion or special position is needed.

### Recommendation
In `blame_internal` (and `Decryption::decrypt_with_proof`), validate `sender`/`recipient` against `self.commitments`/`self.enc_keys` membership with `.get()` and return a defined error/faulty-party result instead of indexing. Similarly, `AdditionalBlameMachine::new` should reject `sender`/`recipient` outside `1 ..= n` at the `blame` boundary. Consider rejecting identity `msg.key`/`pop.R` at `EncryptedMessage::read` for curves where the base `Ciphersuite::read_G` does not.

### Proof of Concept
```rust
// crypto/dkg/pedpop: victim holds an AdditionalBlameMachine for n participants
let machine = AdditionalBlameMachine::<Ristretto>::new(context, n, commitment_msgs).unwrap();

// Attacker submits a blame evaluation naming a participant outside 1..=n.
// Participant::new only rejects 0; n+1 is accepted.
let bogus_sender = Participant::new(n + 1).unwrap();
let msg: EncryptedMessage<Ristretto, SecretShare<_>> =
    EncryptedMessage::read(&mut attacker_bytes.as_ref(), params).unwrap();

// Panics inside blame_internal at self.commitments[&sender]
// (or at self.enc_keys[&decryptor] for an out-of-range recipient)
machine.blame(bogus_sender, recipient, msg, None);
```
Relevant code: `crypto/dkg/pedpop/src/lib.rs:595-604` (`self.commitments[&sender]` index), `crypto/dkg/pedpop/src/encryption.rs:383-395` (`self.enc_keys[&decryptor]` index), `crypto/dkg/pedpop/src/lib.rs:649-662` (map only populated for `1..=n`).

Note: I could not fully confirm whether in-scope callers validate `sender`/`recipient` before reaching `blame`; if a higher layer already bounds these indexes, the practical reachability is reduced and this should be treated as a hardening gap rather than a live exploit.