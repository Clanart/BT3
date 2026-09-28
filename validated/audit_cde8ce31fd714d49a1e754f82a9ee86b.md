### Title
Unauthenticated participant index causes panic during PedPoP blame evaluation - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`AdditionalBlameMachine::blame` and `BlameMachine::blame` accept attacker-controlled `sender` and `recipient` participant indexes. When an optional `EncryptionKeyProof` is supplied, `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` before validating that `recipient` belongs to the DKG participant set. A blame statement naming any nonzero `Participant` greater than `n` therefore panics the verifier.

### Finding Description
`Participant` only enforces that its value is nonzero; it does not enforce membership in a particular threshold set. `AdditionalBlameMachine::new` only registers encryption keys for participants `1..=n`, leaving every larger participant ID absent from `enc_keys`. [1](#0-0) [2](#0-1) 

The public `blame` methods forward attacker-selected indexes directly into `blame_internal` and `decrypt_with_proof`. [3](#0-2) [4](#0-3) 

Once `proof` is `Some`, `decrypt_with_proof` constructs the DLEq statement using `self.enc_keys[&decryptor]`. The indexing operation executes before `DLEqProof::verify`, so even a malformed or dummy proof reaches the panic. [5](#0-4) 

The message’s proof-of-possession does not bind the recipient. Its challenge includes the context, nonce, key, sender, and ciphertext, but no recipient field. Consequently, a valid encrypted share sent by a real participant can be reused in a blame accusation naming an out-of-range recipient. [6](#0-5) 

### Impact Explanation
An unprivileged participant or observer able to submit a blame statement can reliably crash the process evaluating it. In a node or coordinator handling blame reports, this aborts the DKG error-handling path and can terminate the caller rather than returning `PedPoPError` or identifying a faulty participant.

### Likelihood Explanation
The attacker needs:

- a PedPoP context and commitment set accepted by the blame evaluator;
- a message whose PoP verifies for a valid `sender`, which can be a genuine encrypted share previously produced by that sender;
- `proof: Some(_)` containing arbitrary bytes sufficient to deserialize as an `EncryptionKeyProof`;
- a syntactically valid `Participant` such as `n + 1`.

Serialization uses `EncryptedMessage::read` and `EncryptionKeyProof::read`, both of which accept externally supplied bytes. [7](#0-6) [8](#0-7) 

### Recommendation
Validate both `sender` and `recipient` against the registered participant map before evaluating the PoP or proof. `blame_internal` should return an error or a deterministic blame result for unknown participants, and `decrypt_with_proof` should use `HashMap::get` rather than indexing. The accusation API should also reject participants greater than the DKG’s configured `n`.

### Proof of Concept
Conceptual Rust sequence:

```rust
// Assume `n = 3` and all commitment messages were supplied.
let machine = AdditionalBlameMachine::<C>::new(context, 3, commitment_msgs)?;

// `msg` is a genuine EncryptedMessage whose PoP was created for `sender`.
// `proof` only needs to deserialize; it does not need to be valid because the
// vulnerable map lookup happens before proof verification.
let invalid_recipient = Participant::new(4).unwrap();
machine.blame(valid_sender, invalid_recipient, msg, Some(dummy_proof));
```

`dummy_proof.dleq.verify(...)` is never reached as a fallible proof check: evaluating `self.enc_keys[&invalid_recipient]` panics first. [5](#0-4)

### Citations

**File:** crypto/dkg/src/lib.rs (L121-124)
```rust
impl borsh::BorshDeserialize for Participant {
  fn deserialize_reader<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Participant::new(u16::deserialize_reader(reader)?)
      .ok_or_else(|| io::Error::other("invalid participant"))
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

**File:** crypto/dkg/pedpop/src/lib.rs (L674-681)
```rust
  pub fn blame(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    self.0.blame_internal(sender, recipient, msg, proof)
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-176)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L267-269)
```rust
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L302-323)
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
