### Title
Malformed blame accusation can panic on an unregistered recipient - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`AdditionalBlameMachine::blame` accepts attacker-supplied sender and recipient indexes without validating that they were registered during construction. If the accusation contains a valid message proof-of-possession and an arbitrary `EncryptionKeyProof`, `decrypt_with_proof` indexes `self.enc_keys[&decryptor]`; an unregistered recipient causes a panic and denial of service. [1](#0-0) 

### Finding Description
`AdditionalBlameMachine::new` registers encryption keys only for participants `1..=n`. [2](#0-1) 

Its public `blame` method forwards arbitrary `sender` and `recipient` values to `blame_internal` without checking membership in that registered set. [3](#0-2) 

`blame_internal` then calls `Decryption::decrypt_with_proof` with those unchecked identifiers. [4](#0-3) 

When `proof` is `Some`, `decrypt_with_proof` evaluates `self.enc_keys[&decryptor]` while building the DLEq verification points; indexing a `HashMap` with a missing key panics. [1](#0-0) 

The panic is reachable after a successful PoP verification, so the attacker can use a properly formed `EncryptedMessage` rather than malformed bytes. [5](#0-4) 

### Impact Explanation
A remote party able to submit or trigger evaluation of a blame accusation can terminate the calling thread or process. This prevents the DKG blame procedure from completing and can repeatedly abort recovery whenever the malicious accusation is processed. [1](#0-0) 

### Likelihood Explanation
The attacker only needs the public DKG context, a syntactically valid `EncryptionKeyProof`, and an `EncryptedMessage` whose Schnorr PoP verifies for the claimed sender. The PoP challenge is a deterministic transcript over public fields, the sender index, and the ciphertext, while the attacker knows the ephemeral key needed to produce the signature. [6](#0-5) 

No collusion or invalid registered commitments are required; `AdditionalBlameMachine::new` can be populated with fully valid commitment messages, and the panic is caused solely by naming a recipient outside `1..=n`. [2](#0-1) 

### Recommendation
Validate `sender` and `recipient` against the registered participant set at the start of `AdditionalBlameMachine::blame` and `BlameMachine::blame`. Replace `self.enc_keys[&decryptor]` with `get(&decryptor)` and return a defined error or blame result when the key is absent. [1](#0-0) 

### Proof of Concept
Construct a one-participant `AdditionalBlameMachine` using a valid commitment message for participant `1`; this registers only participant `1` in `enc_keys`. [2](#0-1) 

Create an `EncryptedMessage` for sender `1` with arbitrary share-sized ciphertext, ephemeral secret `x`, nonce `r`, `key = xG`, `R = rG`, and `s = r + cx`, where `c` is the public `pop_challenge` output for the context, sender, key, nonce, and ciphertext. [6](#0-5) 

Parse or supply any syntactically valid `EncryptionKeyProof`; its DLEq proof does not need to verify because the missing-recipient lookup is evaluated first. [7](#0-6) 

Call `blame(Participant::new(1), Participant::new(2), msg, Some(proof))` on the `n = 1` machine. The message passes PoP verification, enters the `Some(proof)` branch, and panics on `self.enc_keys[&Participant(2)]`. [8](#0-7)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-177)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L266-269)
```rust
impl<C: Ciphersuite> EncryptionKeyProof<C> {
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-390)
```rust
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L575-583)
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
