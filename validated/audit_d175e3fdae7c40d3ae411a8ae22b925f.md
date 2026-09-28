### Title
Panic on blame accusation with out-of-range participant index causes denial of service - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CVE-2021-32280 (a NULL dereference → DoS in fig2dev's `compute_closed_spline()`), Serai's PedPoP DKG blame flow dereferences attacker-controlled `Participant` indices via `HashMap` indexing (`map[&key]`), which panics when the key is absent. An unprivileged participant who submits a blame accusation naming a `sender` or `recipient` outside the registered DKG set crashes the evaluating party instead of receiving an error.

### Finding Description
`BlameMachine::blame` and `AdditionalBlameMachine::blame` accept `sender: Participant` and `recipient: Participant` plus an `EncryptedMessage` and optional `EncryptionKeyProof` — all fields that originate from an accusing peer's message. They delegate to `blame_internal`, which calls `Decryption::decrypt_with_proof(sender, recipient, msg, proof)`.

Two unchecked indexing sites exist:

1. In `decrypt_with_proof`, when a proof is supplied, the decryptor's registered encryption key is fetched by direct indexing:

```rust
// crypto/dkg/pedpop/src/encryption.rs:381-390
if let Some(proof) = proof {
  proof
    .dleq
    .verify(
      &mut encryption_key_transcript(self.context),
      &[C::generator(), msg.key],
      &[self.enc_keys[&decryptor], *proof.key],   // panics if decryptor never registered
    )
    .map_err(|_| DecryptionError::InvalidProof)?;
```

`enc_keys` is only populated via `Decryption::register`, which is called for exactly the participants in `1..=n` during `verify_r1` (`crypto/dkg/pedpop/src/lib.rs:313-315`) or `AdditionalBlameMachine::new` (`lib.rs:656-660`). `Participant` only enforces non-zero (`Participant::new`, `crypto/dkg/src/lib.rs:29-35`), so any index `n < i <= u16::MAX` is a valid `Participant` yet absent from the map → panic on `self.enc_keys[&decryptor]`.

2. Even when `proof` is `None` and the PoP signature check passes, `blame_internal` later indexes `self.commitments[&sender]` (`crypto/dkg/pedpop/src/lib.rs:599`), which panics identically for an out-of-range `sender`.

No bounds check (`u16::from(x) > n` / `map.contains_key`) is performed on `sender` or `recipient` anywhere in `blame`, `blame_internal`, or `decrypt_with_proof`. Contrast with the signing path, which explicitly validates indices before use (`crypto/frost/src/sign.rs:302-310`).

### Impact Explanation
Any honest node that evaluates a blame accusation — the documented dispute-resolution path of the PedPoP DKG — aborts via panic when the accusation names a non-registered participant. In a deployment where blame evaluation runs in the node's main task (or per the rules, wherever an unprivileged party's public inputs reach this API), a single malicious participant can crash every evaluator that processes their accusation, killing the DKG/signing session host — a denial of service matching the CVE-2021-32280 class (crash via unchecked access on attacker-influenced data), at Medium severity (local, no secret leakage, availability-only).

### Likelihood Explanation
The trigger requires only that the attacker get an honest party to call `blame()`/`AdditionalBlameMachine::blame()` with an accusation whose `sender` or `recipient` is a `Participant` index outside `1..=n` — e.g., `Participant::new(0xFFFF)` in a 3-of-5 DKG. Since blame accusations are, by design, peer-submitted arbitration requests, these fields are attacker-influenced inputs. No collusion, no invalid curve points, and no integrator misuse of the documented kind is needed: the API signature accepts arbitrary `Participant` values and nothing documents that out-of-set indices are UB (the "undefined behavior" caveat in `AdditionalBlameMachine::new` covers the *commitment messages*, not the blame indices).

### Recommendation
Validate `sender` and `recipient` against the registered key set before indexing: replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `.get()` returning a `PedPoPError`/`DecryptionError` variant (e.g., `InvalidParticipant`), or add an explicit `u16::from(x) <= n` / `contains_key` check at the top of `blame_internal` and `AdditionalBlameMachine::blame`, matching the defensive checks used in `ThresholdKeys::view` and `AlgorithmSignMachine::sign`.

### Proof of Concept
```rust
// Honest party completed PedPoP with params t=2, n=3 and holds a BlameMachine
// (or anyone constructs AdditionalBlameMachine::new(context, 3, commitment_msgs)).
// Attacker submits a blame accusation naming a non-existent recipient:

let attacker_msg: EncryptedMessage<C, SecretShare<C::F>> = /* any well-formed or
    attacker-crafted EncryptedMessage, e.g. their own real share message */;
let attacker_proof: Option<EncryptionKeyProof<C>> =
    Some(/* any structurally valid proof the attacker generated for their own key */);

let bogus_recipient = Participant::new(5).unwrap(); // n = 3, so 5 was never registered
let sender = Participant::new(1).unwrap();

// Inside blame_internal -> decrypt_with_proof(sender, 5, msg, Some(proof)):
//   proof.dleq.verify(..., &[self.enc_keys[&decryptor], *proof.key])
// enc_keys only holds {1,2,3} -> HashMap index panic -> abort/DoS.
blame_machine.blame(sender, bogus_recipient, attacker_msg, attacker_proof);

// Alternatively, proof = None with a pop-valid msg reaches
//   self.commitments[&sender]  (lib.rs:599)
// with sender = 5 -> same panic.
```

Note: full certainty on reachability depends on how the integrating node populates `sender`/`recipient` in `blame()` from peer accusations; within the library itself, no validation of those indices exists, so any caller that forwards accusation fields verbatim exposes the panic.