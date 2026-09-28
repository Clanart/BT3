### Title
Panic in `Decryption::decrypt_with_proof` when a blame accusation names the local node as recipient - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CWA-2024-008 (a panic reachable from external inputs halting progress), the PedPoP blame path panics on a missing `HashMap` key when an accusation is evaluated where `recipient` is the local participant. `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept an attacker-influenced `(sender, recipient, msg, proof)` tuple; when `proof` is `Some`, `decrypt_with_proof` evaluates `self.enc_keys[&decryptor]` via `HashMap` indexing, which panics if `decryptor` was never registered. For `BlameMachine` produced by `KeyMachine::calculate_share`, `enc_keys` contains every *other* participant's key but never the local participant's own index (the local party's commitments are consumed via `commitment_msgs`, which is validated to exclude `ours` in `validate_map`). An accusation naming the local node as `recipient` therefore indexes a missing key and panics the process. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`Decryption::register` inserts into `self.enc_keys` only for explicitly registered participants. During `verify_r1`, `register` is called only for entries present in `commitment_msgs`, and `validate_map` requires the map to *exclude* the local participant (`ours`), so `enc_keys` never contains `params.i()`. Later, `BlameMachine::blame(sender, recipient, msg, proof)` forwards `recipient` as `decryptor` into `decrypt_with_proof`. When `proof: Some(...)` is supplied, the expression `self.enc_keys[&decryptor]` at `encryption.rs:388` panics on the absent key before any DLEq verification occurs. A crafted accusation `(sender = any participant, recipient = local node's index, msg = arbitrary EncryptedMessage, proof = Some(garbage))` reaches this via `EncryptedMessage::read`/`EncryptionKeyProof::read` deserialization plus the blame entry point — all inputs are attacker-controlled protocol bytes, not leaked keys or validator internals. [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation
A panic aborts the executing thread. In a Serai node/coordinator context that processes blame accusations in-band, a single unauthenticated-in-effect accusation (sender field plus a trivially constructible `EncryptionKeyProof` encoding — the panic occurs before the proof is checked) crashes the handler, stalling DKG fault adjudication and potentially the surrounding consensus/signing pipeline, mirroring the "panic slows/halts block production" class of the external report.

### Likelihood Explanation
Any remote participant able to submit a blame/accusation message can set `recipient` to the target's `Participant` index and attach a `Some` proof. No valid cryptography is needed — the panic precedes `dleq.verify`. The only requirement is that the deployment evaluates blame for accusations where the recipient is the node itself (the documented purpose of `BlameMachine::blame` is adjudicating disputes over shares sent to a recipient, which includes accusations relayed about the local party).

### Recommendation
Replace `self.enc_keys[&decryptor]` with a checked lookup (`self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)`) in `crypto/dkg/pedpop/src/encryption.rs`. Similarly audit `Decryption::register`'s `assert!` (line 356) and `self.commitments[&sender]` at `pedpop/src/lib.rs:599`, which panic on unregistered `sender` in `blame_internal` — a `sender` outside the known set produces the same reachable panic. Return errors instead of panicking on any party-supplied index.

### Proof of Concept
1. Run PedPoP DKG to completion on a node with `Participant(1)`; obtain `BlameMachine` via `KeyMachine::calculate_share`. Its `Decryption.enc_keys` lacks `Participant(1)`.
2. Submit `blame(sender = Participant(2), recipient = Participant(1), msg = <any EncryptedMessage bytes via EncryptedMessage::read>, proof = Some(EncryptionKeyProof::read(<arbitrary valid encodings>)))`.
3. `blame_internal` → `decrypt_with_proof` → `self.enc_keys[&decryptor]` panics (`HashMap` index on missing key), crashing the node before the proof is verified.

Note: if deployed code only ever calls `blame` with `recipient` restricted to other participants (excluding self), this is unreachable; the panic additionally requires `proof.is_some()`. Severity accordingly Medium (conditional reachability, pure availability impact).

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-362)
```rust
  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    assert!(
      !self.enc_keys.contains_key(&participant),
      "Re-registering encryption key for a participant"
    );
    self.enc_keys.insert(participant, msg.enc_key);
    msg.msg
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-396)
```rust
    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L57-83)
```rust
fn validate_map<T, C: Ciphersuite>(
  map: &HashMap<Participant, T>,
  included: &[Participant],
  ours: Participant,
) -> Result<(), PedPoPError<C>> {
  if (map.len() + 1) != included.len() {
    Err(PedPoPError::IncorrectAmountOfParticipants {
      expected: included.len(),
      found: map.len() + 1,
    })?;
  }

  for included in included {
    if *included == ours {
      if map.contains_key(included) {
        Err(PedPoPError::DkgError(DkgError::DuplicatedParticipant(*included)))?;
      }
      continue;
    }

    if !map.contains_key(included) {
      Err(PedPoPError::MissingParticipant(*included))?;
    }
  }

  Ok(())
}
```

**File:** crypto/dkg/pedpop/src/lib.rs (L311-315)
```rust
    let mut batch = BatchVerifier::<Participant, C::G>::new(commitment_msgs.len());
    let mut commitments = HashMap::new();
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);
```

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

**File:** crypto/dkg/pedpop/src/lib.rs (L623-632)
```rust
  pub fn blame(
    self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> (AdditionalBlameMachine<C>, Participant) {
    let faulty = self.blame_internal(sender, recipient, msg, proof);
    (AdditionalBlameMachine(self), faulty)
  }
```
