### Title

Out-of-range PedPoP blame participant causes an unhandled indexing panic - (File: `crypto/dkg/pedpop/src/lib.rs`)

### Summary

`BlameMachine::blame` and `AdditionalBlameMachine::blame` accept attacker-controlled `sender` and `recipient` participant identifiers without checking that they belong to the DKG participant set. After successful message decryption and share decoding, `blame_internal` indexes `self.commitments[&sender]`; an out-of-range sender therefore causes an indexing panic rather than returning an error.

### Finding Description

`Participant` is only constrained to be a nonzero `u16`; values greater than the configured participant count are representable until `ThresholdParams` performs its own bounds check. [1](#0-0) [2](#0-1) 

`BlameMachine::blame` forwards its `sender`, `recipient`, message, and proof arguments directly to `blame_internal`. [3](#0-2)  `blame_internal` validates the supplied encrypted share and proof, decodes the embedded scalar, and then dereferences `self.commitments[&sender]` to verify the share. [4](#0-3) 

The commitments map is populated only for the configured participant indexes. [5](#0-4)  No check in `blame` or `blame_internal` rejects a sender outside `1..=n`. `AdditionalBlameMachine::blame` exposes the same unchecked path. [6](#0-5) 

### Impact Explanation

A party able to submit a blame accusation or cause one to be evaluated can crash the process evaluating it by specifying a valid nonzero participant ID above `n`. This is remote denial of service of the DKG participant or blame arbiter, matching the malformed-public-input-to-unexpected-process-crash bug class. The panic can interrupt DKG completion or other work performed by the hosting process.

### Likelihood Explanation

The attacker only needs to control the participant identifiers and encrypted-message fields supplied to `blame`. `EncryptedMessage::read` accepts the public key, Schnorr proof-of-possession, and encrypted scalar from attacker-controlled bytes. [7](#0-6) 

The message can remain cryptographically well formed: the PoP transcript binds the chosen sender value, while the ECDH correctness proof is tied to the recipient’s registered encryption key. [8](#0-7)  Thus, an attacker does not need to register the forged `sender`; they need only make the message pass `decrypt_with_proof` so execution reaches the unchecked commitments lookup. If the decryption implementation looks up the forged sender earlier, the same missing-map-entry panic occurs even sooner.

### Recommendation

Validate `sender` and `recipient` at the beginning of `blame_internal`, or inside `blame`, before any map indexing. Return a defined `PedPoPError`/blame result when either participant is outside the committed set. Avoid `HashMap` indexing with attacker-controlled IDs; use `get`/`get_mut` and handle absence explicitly. Add tests with `sender = n + 1`, `recipient = n + 1`, and otherwise valid encrypted shares/proofs.

### Proof of Concept

Conceptually, for a completed `n`-participant PedPoP session:

```rust
let forged_sender = Participant::new(n + 1).unwrap();
let recipient = Participant::new(1).unwrap();

// Construct EncryptedMessage fields over attacker-controlled bytes:
// - fresh scalar r and key = r * G
// - ciphertext encrypting any canonical scalar
// - valid Schnorr PoP whose challenge binds sender = forged_sender
// - valid DLEq proving key and r * recipient_encryption_key
//   share discrete logarithm r
let msg = EncryptedMessage::<C, SecretShare<C::F>>::read(
    &mut forged_bytes.as_slice(),
    params,
).unwrap();
let proof = Some(EncryptionKeyProof::<C>::read(
    &mut forged_proof_bytes.as_slice(),
).unwrap());

// Panics once blame_internal reaches self.commitments[&forged_sender].
let (_additional, _faulty) = blame_machine.blame(
    forged_sender,
    recipient,
    msg,
    proof,
);
```

The decisive missing validation is the unchecked `self.commitments[&sender]` lookup after the message has been accepted and decoded. [9](#0-8)

### Citations

**File:** crypto/dkg/src/lib.rs (L29-35)
```rust
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```

**File:** crypto/dkg/src/lib.rs (L166-178)
```rust
  pub const fn new(t: u16, n: u16, i: Participant) -> Result<ThresholdParams, DkgError> {
    if (t == 0) || (n == 0) {
      return Err(DkgError::ZeroParameter { t, n });
    }

    if t > n {
      return Err(DkgError::InvalidThreshold { t, n });
    }
    if i.0 > n {
      return Err(DkgError::InvalidParticipant { n, participant: i });
    }

    Ok(ThresholdParams { t, n, i })
```

**File:** crypto/dkg/pedpop/src/lib.rs (L511-520)
```rust
    let mut verification_shares = HashMap::new();
    for i in self.params.all_participant_indexes() {
      verification_shares.insert(
        i,
        if i == self.params.i() {
          C::generator() * self.secret.deref()
        } else {
          multiexp_vartime(&exponential::<C>(i, &stripes))
        },
      );
```

**File:** crypto/dkg/pedpop/src/lib.rs (L582-599)
```rust
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L623-630)
```rust
  pub fn blame(
    self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> (AdditionalBlameMachine<C>, Participant) {
    let faulty = self.blame_internal(sender, recipient, msg, proof);
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
