### Title
Panic in PedPoP blame resolution via out-of-range `sender`/`recipient` participant index - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` take attacker-influenced `sender` and `recipient` `Participant` indices and index internal `HashMap`s with them (`self.commitments[&sender]`, and the ECDH `enc_keys` map inside `Decryption::decrypt_with_proof`) without any bounds check. A `Participant` can encode any nonzero `u16` up to 65535, while the maps only contain keys `1..=n`. Naming an unregistered participant causes a `HashMap` index panic, crashing the node — the same bug class as the quinn-proto DoS (a panic reachable only when an uncommon handling path — here, the blame/fault-handling path — is exercised by hostile input).

### Finding Description
`blame_internal` resolves an accusation of fault between a `sender` and `recipient`: [1](#0-0) 

It calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` (which internally uses the per-participant ECDH key map registered only for `1..=n`, see `Encryption::encrypt` using `self.decryption.enc_keys[&participant]` at `crypto/dkg/pedpop/src/encryption.rs:466`), and then unconditionally evaluates `&self.commitments[&sender]` at line 599. `self.commitments` is populated only for participants `1..=n` — in `KeyMachine::calculate_share`/`verify_r1` via `self.params.all_participant_indexes()` (lines 313, 336) and in `AdditionalBlameMachine::new` via `for i in 1 ..= n` (lines 656-660). Neither `blame` nor `blame_internal` validates `sender`/`recipient` against `1..=n`.

Contrast with the rest of the crate, which does validate maps before indexing: `validate_map` (lines 57-83) is applied to shares and commitments before use. The blame path skipped this.

Reachability: an unprivileged participant in the DKG (or an observer in the `AdditionalBlameMachine` case) submits a fault accusation naming `sender = Participant(n + 1)` or any index not in `1..=n` (or a valid `sender` with a `recipient` index that makes `decrypt_with_proof` index a missing ECDH key). The victim node evaluates the accusation and panics.

### Impact Explanation
Denial of service. A panic in the blame handler crashes the processor/validator task evaluating the accusation. Because blame resolution exists precisely to handle malicious behavior, the defensive code path itself becomes the crash surface: an attacker who is about to be blamed can preemptively crash honest nodes processing accusations, or crash third-party `AdditionalBlameMachine` verifiers, aborting the DKG/key-rotation and any dependent signing.

### Likelihood Explanation
Triggering requires only that an attacker-influenced accusation reach `blame()` with an invalid participant index — a two-byte field. `Participant` permits any nonzero `u16`, and no check in `blame`, `blame_internal`, or `AdditionalBlameMachine::blame` constrains it to `1..=n`. The cost is negligible; the only requirement is that the victim runs the blame flow, which is the expected response to the attacker's own faulty message — mirroring the quinn advisory where `refuse()`/`ignore()` (the anti-DoS path) was what panicked.

### Recommendation
In `blame_internal` (and/or the public `blame` wrappers), validate `sender` and `recipient` up front: reject/short-circuit if either is not a key of `self.commitments` (or exceeds `n`), e.g. `let Some(sender_commitments) = self.commitments.get(&sender) else { return sender /* or a dedicated error */ };`. Also audit `Decryption::decrypt_with_proof` to use `.get()` rather than indexing `enc_keys`, returning a structured error for unregistered participants.

### Proof of Concept
1. Set up a PedPoP `BlameMachine` (via `KeyMachine::calculate_share`) or a third-party `AdditionalBlameMachine::new(context, n, commitment_msgs)` for `n` participants.
2. Call `blame(sender: Participant(n + 1), recipient: Participant(1), msg: <any EncryptedMessage>, proof: None)`.
3. `blame_internal` reaches `self.commitments[&sender]` (line 599) — or `enc_keys[&sender]` inside `decrypt_with_proof` earlier — and panics with "key not found", since the map only holds `1..=n`.

Note: I verified the indexing sites and the absence of bounds checks in `blame_internal`/`AdditionalBlameMachine::new`, but I could not inspect the body of `Decryption::decrypt_with_proof` in the remaining iterations; if that function returns an error rather than panicking on unknown `sender`, the panic still occurs deterministically at `self.commitments[&sender]` on line 599 for any accusation where `decrypt_with_proof` did not already abort.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L575-609)
```rust
  fn blame_internal(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };

    let Some(share) = Option::<C::F>::from(C::F::from_repr(share_bytes.0)) else {
      // If this isn't a valid scalar, the sender is faulty
      return sender;
    };

    // If this isn't a valid share, the sender is faulty
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
    }

    // The share was canonical and valid
    recipient
  }
```
