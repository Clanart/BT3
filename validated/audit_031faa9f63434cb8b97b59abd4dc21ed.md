### Title
Untrusted blame participants cause panics via unguarded `HashMap` indexing in `Decryption::decrypt_with_proof` - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2016-3183 is an out-of-bounds read on attacker-controlled dimensions in an image decoder, yielding denial of service. The Serai analog is a bounds/index panic reachable from untrusted, message-carried `Participant` values: `Decryption::decrypt_with_proof` indexes `self.enc_keys` with the caller-supplied `decryptor` participant, which panics (the safe-Rust equivalent of an out-of-bounds access) whenever the blamed "recipient" was never registered in the `1 ..= n` key set. This is reached by `AdditionalBlameMachine::blame`, driven by the processor's `CoordinatorMessage::VerifyBlame` handler.

### Finding Description
`AdditionalBlameMachine::new` registers encryption keys only for participants `1 ..= n` [1](#0-0) . `blame(sender, recipient, msg, proof)` forwards both attacker-influenced `Participant` values to `blame_internal` [2](#0-1) , which calls `decrypt_with_proof` with `recipient` as `decryptor`. Inside `decrypt_with_proof`, the DLEq verification directly indexes `self.enc_keys[&decryptor]` [3](#0-2) . `std::collections::HashMap`'s `Index` impl panics on a missing key, so any `decryptor` outside the registered `1 ..= n` range (e.g., a syntactically valid but out-of-range `Participant` decoded from a message) crashes the thread instead of returning an error. No range check on `sender`/`recipient` against `n` exists in `AdditionalBlameMachine::blame`.

The processor reaches this path in `CoordinatorMessage::VerifyBlame`, where `accuser` and `accused` come from the deserialized coordinator message and are passed verbatim to `AdditionalBlameMachine::new(...).blame(accuser, accused, substrate_share, substrate_blame)` without being validated against `params.n()` [4](#0-3) . `Participant::read`/`Participant::new` only rejects zero, so `n < participant <= u16::MAX` values are representable.

### Impact Explanation
A single crafted blame message with an out-of-range `accuser`/`accused` panics the processor task evaluating blame. This is a remotely triggerable denial of service of the blame-evaluation path during key generation, matching the CVE's availability-only impact class (OOB read → crash). Depending on how the processor's async runtime handles the panic, it can kill the key-gen/blame handling loop, halting threshold key generation.

### Likelihood Explanation
Reachability requires an unprivileged party to cause a `VerifyBlame` message bearing an out-of-range `Participant` to be processed. The blame contents (`share`, `blame`) are already attacker-controlled byte strings read via `EncryptedMessage::read`/`EncryptionKeyProof::read` [5](#0-4) , and `accuser`/`accused` are taken from the message itself. The attack cost is low; the main uncertainty is whether any upstream routing layer rejects out-of-range participants before `blame` is invoked — the in-scope crypto code itself performs no such check.

### Recommendation
Validate `sender` and `recipient` against `params.n()` in `AdditionalBlameMachine::blame` (and/or in `VerifyBlame` handling) and replace `self.enc_keys[&decryptor]` with `self.enc_keys.get(&decryptor)` returning `DecryptionError::InvalidProof` on absence, so malformed blame messages produce errors instead of panics.

### Proof of Concept
```rust
// Conceptual: with n = 5, build a valid AdditionalBlameMachine over 5
// commitment messages, then call:
machine.blame(
    Participant::new(6).unwrap(), // out-of-range accuser
    Participant::new(6).unwrap(), // out-of-range accused/decryptor
    any_encrypted_message_with_valid_pop,
    Some(valid_encryption_key_proof),
);
// decrypt_with_proof evaluates self.enc_keys[&decryptor] where
// decryptor = Participant(6), which was never registered for 1..=5,
// causing HashMap::index to panic -> process crash (DoS).
```

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L649-661)
```rust
  pub fn new(
    context: [u8; 32],
    n: u16,
    mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
  ) -> Result<Self, PedPoPError<C>> {
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
```

**File:** crypto/dkg/pedpop/src/lib.rs (L674-682)
```rust
  pub fn blame(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    self.0.blame_internal(sender, recipient, msg, proof)
  }
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
