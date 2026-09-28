### Title
Missing-participant lookups in PedPoP blame handling panic on attacker-named participants — ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
The DKG blame path (`BlameMachine::blame` / `AdditionalBlameMachine`, used by the processor's `VerifyBlame` handler) performs unchecked `HashMap` indexing on participant indexes taken from the incoming accusation. If the accused (or accusing) participant never submitted a round-1 `EncryptionKeyMessage`, `Decryption::decrypt_with_proof` panics on `self.enc_keys[&decryptor]`, and `blame_internal` can likewise panic on `self.commitments[&sender]` — turning a crafted protocol message into a crash/abort of the DKG handling task, analogously to CVE-2024-25583's "crafted response causes denial of service" class.

### Finding Description
`Decryption::register` only inserts an encryption key for participants who actually delivered a commitment message, and `assert!`s on re-registration [1](#0-0) . During blame resolution, `decrypt_with_proof` indexes `self.enc_keys[&decryptor]` with no presence check [2](#0-1) , and `blame_internal` indexes `self.commitments[&sender]` the same way [3](#0-2) . `blame()` accepts arbitrary `sender`/`recipient` `Participant` values with no validation that they correspond to registered parties [4](#0-3) . The processor wires message-supplied `accuser`/`accused` indexes straight into this API in the `VerifyBlame` arm [5](#0-4) . Unlike the normal paths, `validate_map` is never called on the blame inputs, so there is no `InvalidParticipant`/`MissingParticipant` rejection before the panicking index.

### Impact Explanation
A panic here crashes the task executing `handle_machine`/`VerifyBlame`, which in a validator/processor deployment aborts the key-generation or blame-adjudication flow (and, depending on panic propagation, the whole processor). This is a remote-triggerable denial of service: the crash is reached solely from protocol message fields (`accuser`, `accused`, `share`, `blame`) an unauthenticated-by-design accusation flow carries.

### Likelihood Explanation
Any participant able to submit (or relay) a blame/`VerifyBlame` message naming a participant index that is valid (`1..=n`) but which never sent round-1 commitments — e.g., an index reserved for an offline or already-excluded validator — triggers the panic. The lookups exist precisely because `enc_keys`/`commitments` are sparse maps over participants; the code assumes every named participant completed round 1, an assumption blame traffic can violate. Severity is Medium: availability impact only, no key/share compromise, and reachability depends on the integration exposing blame with non-registered participants.

### Recommendation
Replace indexing with checked lookups: in `Decryption::decrypt_with_proof`, return `DecryptionError::InvalidProof` (or a dedicated `UnknownParticipant` variant) when `enc_keys.get(&decryptor)` is `None`; in `blame_internal`, treat a missing `commitments[&sender]` as `sender` being faulty or return an explicit error rather than panicking. Optionally validate `sender`/`recipient` against `params.all_participant_indexes()` in `blame()`/`AdditionalBlameMachine::new` before touching the maps.

### Proof of Concept
1. Run PedPoP with `n` participants where participant `k` withholds its round-1 `EncryptionKeyMessage` (so no `enc_keys[k]` entry exists). Other participants complete round 1 and round 2, yielding a `BlameMachine`/`AdditionalBlameMachine`.
2. Submit a blame invocation `blame(accuser, k, msg, proof)` (or via `CoordinatorMessage::VerifyBlame { accuser, accused: k, .. }` against a machine rebuilt by `AdditionalBlameMachine::new` from the stored commitments).
3. `blame_internal` calls `self.encryption.decrypt_with_proof(sender, recipient=k, ...)`, which evaluates `self.enc_keys[&k]` — an absent key — and panics instead of returning `Err`. Equivalent panic occurs at `self.commitments[&sender]` in `blame_internal` when `sender` lacks stored commitments.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-361)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-390)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L596-604)
```rust
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
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

**File:** processor/src/key_gen.rs (L504-549)
```rust
      CoordinatorMessage::VerifyBlame { id, accuser, accused, share, blame } => {
        let params = ParamsDb::get(txn, &id.session, id.attempt).unwrap().0;

        let mut share_ref = share.as_slice();
        let Ok(substrate_share) = EncryptedMessage::<
          Ristretto,
          SecretShare<<Ristretto as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        let Ok(network_share) = EncryptedMessage::<
          N::Curve,
          SecretShare<<N::Curve as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        if !share_ref.is_empty() {
          return ProcessorMessage::Blame { id, participant: accused };
        }

        let mut substrate_commitment_msgs = HashMap::new();
        let mut network_commitment_msgs = HashMap::new();
        let commitments = CommitmentsDb::get(txn, &id).unwrap();
        for (i, commitments) in commitments {
          let mut commitments = commitments.as_slice();
          substrate_commitment_msgs
            .insert(i, EncryptionKeyMessage::<_, _>::read(&mut commitments, params).unwrap());
          network_commitment_msgs
            .insert(i, EncryptionKeyMessage::<_, _>::read(&mut commitments, params).unwrap());
        }

        // There is a mild DoS here where someone with a valid blame bloats it to the maximum size
        // Given the ambiguity, and limited potential to DoS (this being called means *someone* is
        // getting fatally slashed) voids the need to ensure blame is minimal
        let substrate_blame =
          blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());
        let network_blame =
          blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());

        let substrate_blame = AdditionalBlameMachine::new(
          context(&id, SUBSTRATE_KEY_CONTEXT),
          params.n(),
          substrate_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, substrate_share, substrate_blame);
```
