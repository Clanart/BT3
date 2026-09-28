### Title
PedPoP reveals an ECDH blame key before authenticating the encrypted share - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` decrypts an untrusted `EncryptedMessage` and constructs an `EncryptionKeyProof` before the batched proof-of-possession has actually been verified. [1](#0-0) [2](#0-1)  If the decrypted bytes are not a canonical scalar, it immediately returns that proof, bypassing the later `batch.verify_with_vartime_blame()` call that would classify the invalid PoP as `blame: None`. [3](#0-2) 

### Finding Description
An `EncryptedMessage` contains an ephemeral public key `key`, a Schnorr proof of possession `pop`, and encrypted bytes. [4](#0-3)  The PoP is bound to the context, sender, key, nonce, and ciphertext, so an attacker who copies another message's key cannot produce a valid PoP for a modified ciphertext. [5](#0-4) 

However, `Encryption::decrypt` only queues that PoP into the supplied `BatchVerifier`, then computes the victim's ECDH point and returns an `EncryptionKeyProof` for it. [2](#0-1)  `calculate_share` consumes the returned plaintext before executing the batch verifier, and its canonical-scalar error path embeds the proof in `PedPoPError::InvalidShare`. [6](#0-5)  This reintroduces the exact copied-key disclosure described by the `EncryptedMessage` security comment. [7](#0-6) 

### Impact Explanation
The disclosed proof contains the ECDH point and a DLEq proof tying it to the victim's registered encryption key and the attacker-supplied ephemeral key. [8](#0-7) [9](#0-8)  If that ephemeral key was copied from a genuine encrypted share, the returned key decrypts that genuine message and reveals its Pedersen secret-share component. [10](#0-9) 

### Likelihood Explanation
A party supplying `shares` to `calculate_share` can copy an observed message's `key`, replace the ciphertext and PoP, and submit it as the corresponding participant's share. [11](#0-10)  The attack succeeds whenever the decrypted bytes fail `C::F::from_repr`; for curves whose scalar representation is wider than the scalar modulus, random malformed ciphertext has a substantial chance of reaching this early-return path. [12](#0-11) [13](#0-12) 

### Recommendation
Do not return or expose an `EncryptionKeyProof` until the message's PoP has been verified successfully. In `calculate_share`, run the decryption-PoP batch check before canonical-scalar processing, or make `Encryption::decrypt` return an error type that suppresses the generated proof until the caller confirms the queued PoP was valid. [14](#0-13) [15](#0-14) 

### Proof of Concept
1. Observe a genuine `EncryptedMessage<C, SecretShare<C::F>>` sent to the victim and retain its `key` and ciphertext. [16](#0-15) 
2. Construct a forged message with the same `key`, arbitrary ciphertext bytes that parse as `SecretShare`, and an invalid Schnorr `pop`. [17](#0-16) 
3. Submit it to the victim's `KeyMachine::calculate_share`; `Encryption::decrypt` queues the invalid PoP but still derives and returns the ECDH key proof. [2](#0-1) 
4. When the forged ciphertext decrypts to a non-canonical scalar, `calculate_share` returns `InvalidShare { blame: Some(proof) }` before checking the invalid PoP. [13](#0-12) 
5. Serialize the returned `EncryptionKeyProof`, extract its `key`, and apply `cipher(context, key)` to the observed genuine ciphertext to recover the original share bytes. [18](#0-17) [19](#0-18)

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L263-273)
```rust
impl<F: PrimeField> ReadWrite for SecretShare<F> {
  fn read<R: Read>(reader: &mut R, _: ThresholdParams) -> io::Result<Self> {
    let mut repr = F::Repr::default();
    reader.read_exact(repr.as_mut())?;
    Ok(SecretShare(repr))
  }

  fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.0.as_ref())
  }
}
```

**File:** crypto/dkg/pedpop/src/lib.rs (L357-369)
```rust
    // Step 1: Generate secret shares for all other parties
    let mut res = HashMap::new();
    for l in self.params.all_participant_indexes() {
      // Don't insert our own shares to the byte buffer which is meant to be sent around
      // An app developer could accidentally send it. Best to keep this black boxed
      if l == self.params.i() {
        continue;
      }

      let mut share = polynomial(&self.coefficients, l);
      let share_bytes = Zeroizing::new(SecretShare::<C::F>(share.to_repr()));
      share.zeroize();
      res.insert(l, self.encryption.encrypt(rng, l, share_bytes));
```

**File:** crypto/dkg/pedpop/src/lib.rs (L463-499)
```rust
  pub fn calculate_share<R: RngCore + CryptoRng>(
    mut self,
    rng: &mut R,
    mut shares: HashMap<Participant, EncryptedMessage<C, SecretShare<C::F>>>,
  ) -> Result<BlameMachine<C>, PedPoPError<C>> {
    validate_map(
      &shares,
      &self.params.all_participant_indexes().collect::<Vec<_>>(),
      self.params.i(),
    )?;

    let mut batch = BatchVerifier::new(shares.len());
    let mut blames = HashMap::new();
    for (l, share_bytes) in shares.drain() {
      let (mut share_bytes, blame) =
        self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
      share_bytes.zeroize();
      *self.secret += share.deref();

      blames.insert(l, blame);
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
    }
    batch.verify_with_vartime_blame().map_err(|id| {
      let (l, blame) = match id {
        BatchId::Decryption(l) => (l, None),
        BatchId::Share(l) => (l, Some(blames.remove(&l).unwrap())),
      };
      PedPoPError::InvalidShare { participant: l, blame }
    })?;
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L80-92)
```rust
#[derive(Clone, Zeroize)]
pub struct EncryptedMessage<C: Ciphersuite, E: Encryptable> {
  key: C::G,
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
  pop: SchnorrSignature<C>,
  msg: Zeroizing<E>,
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-131)
```rust
fn cipher<C: Ciphersuite>(context: [u8; 32], ecdh: &Zeroizing<C::G>) -> ChaCha20 {
  // Ideally, we'd box this transcript with ZAlloc, yet that's only possible on nightly
  // TODO: https://github.com/serai-dex/serai/issues/151
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"encryption_key");

  let mut ecdh = ecdh.to_bytes();
  transcript.append_message(b"shared_key", ecdh.as_ref());
  ecdh.as_mut().zeroize();

  let zeroize = |buf: &mut [u8]| buf.zeroize();

  let mut key = Cc20Key::default();
  let mut challenge = transcript.challenge(b"key");
  key.copy_from_slice(&challenge[.. 32]);
  zeroize(challenge.as_mut());

  // Since the key is single-use, it doesn't matter what we use for the IV
  // The issue is key + IV reuse. If we never reuse the key, we can't have the opportunity to
  // reuse a nonce
  // Use a static IV in acknowledgement of this
  let mut iv = Cc20Iv::default();
  // The \0 is to satisfy the length requirement (12), not to be null terminated
  iv.copy_from_slice(b"DKG IV v0.2\0");

  // ChaCha20 has the same commentary as the transcript regarding ZAlloc
  // TODO: https://github.com/serai-dex/serai/issues/151
  let res = ChaCha20::new(&key, &iv);
  zeroize(key.as_mut());
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L259-279)
```rust
/// A proof that the provided encryption key is a legitimately derived shared key for some message.
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub struct EncryptionKeyProof<C: Ciphersuite> {
  key: Zeroizing<C::G>,
  dleq: DLEqProof<C::G>,
}

impl<C: Ciphersuite> EncryptionKeyProof<C> {
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.key.to_bytes().as_ref())?;
    self.dleq.write(writer)
  }

  pub fn serialize(&self) -> Vec<u8> {
    let mut buf = vec![];
    self.write(&mut buf).unwrap();
    buf
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-393)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L469-500)
```rust
  pub(crate) fn decrypt<R: RngCore + CryptoRng, I: Copy + Zeroize, E: Encryptable>(
    &self,
    rng: &mut R,
    batch: &mut BatchVerifier<I, C::G>,
    // Uses a distinct batch ID so if this batch verifier is reused, we know its the PoP aspect
    // which failed, and therefore to use None for the blame
    batch_id: I,
    from: Participant,
    mut msg: EncryptedMessage<C, E>,
  ) -> (Zeroizing<E>, EncryptionKeyProof<C>) {
    msg.pop.batch_verify(
      rng,
      batch,
      batch_id,
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    );

    let key = ecdh::<C>(&self.enc_key, msg.key);
    cipher::<C>(self.context, &key).apply_keystream(msg.msg.as_mut().as_mut());
    (
      msg.msg,
      EncryptionKeyProof {
        key,
        dleq: DLEqProof::prove(
          rng,
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &self.enc_key,
        ),
      },
    )
```
