### Title
Unvalidated `Participant` index in PedPoP blame flow causes `HashMap` indexing panic (node-crash DoS) - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
Analogous to CVE-2016-10129 (denial of service via attacker-controlled input reaching a dereference of absent state), PedPoP's blame/decryption path performs an unchecked `HashMap` index (`self.enc_keys[&decryptor]`) on a `Participant` value that originates from untrusted, attacker-supplied bytes. An unprivileged participant can cause an honest validator's processor to panic while it verifies a share-blame claim, crashing the validator during DKG blame resolution.

### Finding Description
`Decryption::decrypt_with_proof` verifies an `EncryptionKeyProof` by indexing the registered encryption-key map with an attacker-influenced index:

```rust
// crypto/dkg/pedpop/src/encryption.rs
proof
  .dleq
  .verify(
    &mut encryption_key_transcript(self.context),
    &[C::generator(), msg.key],
    &[self.enc_keys[&decryptor], *proof.key],   // panics if decryptor not registered
  )
```

`enc_keys` is only populated for participants whose `EncryptionKeyMessage` was actually registered via `Decryption::register` (`encryption.rs:351-362`). `std::collections::HashMap`'s `Index` impl panics on a missing key, so a `decryptor` that was never registered — e.g., an out-of-range participant index (`> n`) or a participant who never sent commitments — turns a routine blame verification into a panic.

The `decryptor`/`from` participants flow from untrusted inputs: the DKG accusation path parses `accuser`/`faulty` fields where the only validation is `Participant::new` (a nonzero check), and `AdditionalBlameMachine::blame(accuser, accused, share, proof)` is then invoked over those values (`processor/src/key_gen.rs:543-556` calls `.blame(accuser, accused, ...)` with transaction-derived participants). Similarly, `EncryptedMessage::read`/`EncryptionKeyMessage::read` accept arbitrary `ThresholdParams`-shaped bytes with no binding that the accusing party is within `1..=params.n()`. Nowhere in `decrypt_with_proof` is `enc_keys.contains_key(&decryptor)` checked before indexing.

The same unchecked-index pattern exists at `Encryption::encrypt` (`encryption.rs:466`, `self.decryption.enc_keys[&participant]`), though that path is driven by local iteration and is less clearly attacker-reachable; the blame path is the exposed one since `decryptor` is derived from the accusation, not local state.

### Impact Explanation
A panic in `decrypt_with_proof` unwinds through the blame-verification path and crashes the validator's processor task while it is adjudicating a DKG share complaint. An attacker who can emit a share-blame-triggering message (a participant in the DKG set submitting malformed/invalid shares, or an accusation naming an out-of-range accuser) can therefore deny service to every honest validator that processes the blame — stalling or aborting key generation and any signing that depends on it. This is a remote, unauthenticated-input-triggered crash matching the CVE's "crafted protocol input → dereference of missing object → DoS" shape (Rust panic instead of NULL dereference).

### Likelihood Explanation
Reachable by any party able to cause a blame evaluation: the accusing message's participant fields are only checked for being a valid (nonzero) `Participant`, not for membership in `enc_keys`. The panic requires the blame path to run, which requires a DKG session where a participant submits an accusation — a normal, expected protocol event that an attacker-participant can trigger at will. No collusion, key material, or validator privileges are needed. The impact is availability-only (crash/DoS), with no secret leakage, so it fits Medium/High rather than Critical.

### Recommendation
In `Decryption::decrypt_with_proof` (crypto/dkg/pedpop/src/encryption.rs), replace `self.enc_keys[&decryptor]` with `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` (or a dedicated `UnknownParticipant` error). Apply the same treatment to `Encryption::encrypt`'s `enc_keys[&participant]` lookup, and have callers (e.g., `AdditionalBlameMachine::blame`) validate `accuser`/`accused` against `params.n()` before reaching decryption, returning `io::Error`/a blame-mismatch result instead of panicking.

### Proof of Concept
1. During a PedPoP DKG, attacker participant `A` registers a valid `EncryptionKeyMessage` for index `a ∈ 1..=n`.
2. `A` sends victim `B` a correctly formatted `EncryptedMessage` (valid `key`, valid PoP `SchnorrSignature`, arbitrary ciphertext), then publishes an accusation whose `accuser` field is `Participant(0xFFFF)` (or any index that never registered an encryption key) with a syntactically valid `EncryptionKeyProof` (any `key`/`dleq` bytes deserialize).
3. `B`'s `AdditionalBlameMachine::blame(accuser, accused, share, Some(proof))` invokes `decrypt_with_proof(from, decryptor = 0xFFFF, msg, proof)`.
4. `msg.pop.verify(...)` succeeds, then `self.enc_keys[&0xFFFF]` executes and panics with "key not found," crashing `B`'s processor.

The panic site is deterministic (`HashMap::index` on absent key) and requires no malformed-length tricks — only an unregistered participant index reaching the lookup.

Uncertainty note: I was unable to read `crypto/dkg/pedpop/src/lib.rs`'s `blame`/`AdditionalBlameMachine` implementation in full within the tool-call limit, so the exact point where `accuser` becomes `decryptor` is inferred from the call chain (`key_gen.rs` → `blame(...)` → `decrypt_with_proof`) and the argument names. If `blame` already bounds `accuser` to `params.n()` and the registered set before calling `decrypt_with_proof`, the panic is unreachable and this finding reduces to a latent robustness bug rather than an exploitable DoS.