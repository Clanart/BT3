### Title
Out-of-range `Participant` in PedPoP blame flow panics the process - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`AdditionalBlameMachine::blame` accepts `sender`/`recipient` as `Participant`, but `Participant::new` only rejects zero and does not bound IDs to the DKG’s `n` [1](#0-0) . `AdditionalBlameMachine::new` registers encryption keys only for `1 ..= n`, leaving any `Participant > n` absent from `enc_keys` [2](#0-1) . During blame evaluation, `decrypt_with_proof` indexes `self.enc_keys[&decryptor]`, which panics on an out-of-range `recipient` before returning an error [3](#0-2) .

### Finding Description
The panic is reachable through the public blame API: `AdditionalBlameMachine::blame` forwards caller-controlled `sender`, `recipient`, an `EncryptedMessage`, and an optional `EncryptionKeyProof` into `blame_internal` [4](#0-3) . `blame_internal` calls `decrypt_with_proof` without first validating that `sender` and `recipient` are within `1 ..= n` [5](#0-4) . For a `recipient` outside the registered set, the `HashMap` index `self.enc_keys[&decryptor]` panics while constructing the DLEq verification points [6](#0-5) . For a `sender` outside the commitment set with a valid proof/message, the subsequent share check indexes `self.commitments[&sender]` and can also panic [7](#0-6) .

### Impact Explanation
An unprivileged party able to submit or trigger evaluation of a blame statement can name a nonzero participant ID greater than `n` and cause a deterministic panic rather than a `PedPoPError`, denying service by crashing the blame adjudication path [4](#0-3) . This mirrors the externally reported class of a reachable input causing a full availability loss of the component processing it [3](#0-2) .

### Likelihood Explanation
The reachable path requires only a parsed `EncryptedMessage`/`EncryptionKeyProof` and attacker-chosen `Participant` values passed to `blame`; no threshold compromise, leaked key, or invalid curve construction is required [8](#0-7) . The only gate is that `msg.pop` must verify for the claimed `from`, which is a public Schnorr proof-of-possession over fields the attacker can generate for an arbitrary sender ID [9](#0-8) . Since `Participant` itself permits every nonzero `u16`, no deserialization layer rejects `u16::MAX` before the panic [1](#0-0) .

### Recommendation
Validate `sender` and `recipient` in `blame_internal`/`AdditionalBlameMachine::blame` against `1 ..= n` before indexing `enc_keys` or `commitments`, returning `PedPoPError::MissingParticipant`/`InvalidParticipant` instead of panicking [5](#0-4) . Replace `HashMap` indexing with `get`/`ok_or` on lookups such as `self.enc_keys[&decryptor]` and `self.commitments[&sender]` so out-of-range IDs cannot abort execution [3](#0-2) .

### Proof of Concept
Conceptually, construct `AdditionalBlameMachine` for `n = 2`, then call `blame` with `recipient = Participant::new(u16::MAX).unwrap()` while supplying any `msg` whose PoP verifies for `sender = Participant::new(1)` and `proof = Some(...)`; evaluation reaches `decrypt_with_proof` and panics at `self.enc_keys[&decryptor]` because only recipients `1` and `2` were registered [10](#0-9) [11](#0-10) .

### Citations

**File:** crypto/dkg/src/lib.rs (L27-35)
```rust
impl Participant {
  /// Create a new Participant identifier from a u16.
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L575-588)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L595-603)
```rust
    // If this isn't a valid share, the sender is faulty
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
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

**File:** crypto/dkg/pedpop/src/lib.rs (L654-661)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L302-324)
```rust
fn pop_challenge<C: Ciphersuite>(
  context: [u8; 32],
  nonce: C::G,
  key: C::G,
  sender: Participant,
  msg: &[u8],
) -> C::F {
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption Key Proof of Possession v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"proof_of_possession");

  transcript.append_message(b"nonce", nonce.to_bytes());
  transcript.append_message(b"key", key.to_bytes());
  // This is sufficient to prevent the attack this is meant to stop
  transcript.append_message(b"sender", sender.to_bytes());
  // This, as written above, doesn't hurt
  transcript.append_message(b"message", msg);
  // While this is a PoK and a PoP, it's called a PoP here since the important part is its owner
  // Elsewhere, where we use the term PoK, the important part is that it isn't some inverse, with
  // an unknown to anyone discrete log, breaking the system
  C::hash_to_F(b"DKG-encryption-proof_of_possession", &transcript.challenge(b"schnorr"))
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
