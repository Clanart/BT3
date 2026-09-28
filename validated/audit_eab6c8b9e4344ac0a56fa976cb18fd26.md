### Title
Unauthenticated blame inputs can panic on unregistered participant indexes - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
`BlameMachine::blame` and `AdditionalBlameMachine::blame` accept caller-controlled `sender` and `recipient` indexes, but `Decryption::decrypt_with_proof` and `blame_internal` index participant maps without first checking membership. A blame request naming an unregistered recipient, or a successfully proven message naming an unregistered sender, panics rather than returning a blame result. [1](#0-0) [2](#0-1) 

### Finding Description
`decrypt_with_proof` evaluates `self.enc_keys[&decryptor]` when constructing the DLEQ verification statements. `enc_keys` only contains participants registered from the commitment set, so a `recipient` outside the registered set causes a `HashMap` index panic. [3](#0-2) 

If the supplied message and proof are valid and decryption succeeds, `blame_internal` subsequently evaluates `self.commitments[&sender]` while checking the secret share. A `sender` outside the commitment map causes another panic. [4](#0-3) 

Both public `blame` entry points expose this behavior: `BlameMachine::blame` delegates to `blame_internal`, and `AdditionalBlameMachine::blame` does the same without validating that `sender` or `recipient` is within `1..=n`. [5](#0-4)  

### Impact Explanation
This is a remotely triggerable denial of service in an application that evaluates blame accusations from unauthenticated or partially authenticated protocol messages. The panic aborts the caller instead of identifying either participant as faulty, enabling repeated request-level crashes and preventing blame adjudication. 

### Likelihood Explanation
The recipient-side panic only requires an otherwise validly deserialized `EncryptedMessage` with a valid PoP, an out-of-range `recipient`, and a decodable `EncryptionKeyProof`. The sender-side panic requires a valid DLEQ proof for the chosen message key and a valid registered recipient, after which an out-of-range `sender` reaches the unchecked `commitments` lookup.  

### Recommendation
Before evaluating blame, reject `sender` and `recipient` values absent from `encryption.enc_keys` or `commitments`, returning a deterministic `PedPoPError` or defined blame result. Replace indexing at `enc_keys[&decryptor]` and `commitments[&sender]` with `get`/`ok_or` handling so malformed blame references cannot panic.  

### Proof of Concept
```rust
// After constructing a BlameMachine or AdditionalBlameMachine for n participants,
// obtain a validly encoded EncryptedMessage and EncryptionKeyProof, then call:
let bogus = Participant::new(n + 1).unwrap();

// Panic path 1:
// decrypt_with_proof evaluates self.enc_keys[&bogus] before DLEqProof::verify.
machine.blame(valid_sender, bogus, encrypted_msg, Some(key_proof));

// Panic path 2:
// With recipient registered and the DLEq proof valid, blame_internal later evaluates
// self.commitments[&bogus].
machine.blame(bogus, valid_recipient, encrypted_msg, Some(key_proof));
```

The first call panics in `Decryption::decrypt_with_proof`; the second panics in `BlameMachine::blame_internal`.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L366-393)
```rust
  pub(crate) fn decrypt_with_proof<E: Encryptable>(
    &self,
    from: Participant,
    decryptor: Participant,
    mut msg: EncryptedMessage<C, E>,
    // There's no encryption key proof if the accusation is of an invalid signature
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Result<Zeroizing<E>, DecryptionError> {
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }

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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L575-608)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L623-631)
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
```
