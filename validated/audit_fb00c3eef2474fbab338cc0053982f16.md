### Title
PedPoP reveals share-decryption keys before validating the message proof-of-possession - ([File: crypto/dkg/pedpop/src/lib.rs](http://file))

### Summary
`KeyMachine::calculate_share` returns a blame `EncryptionKeyProof` when a decrypted share is non-canonical before verifying the queued Schnorr proof-of-possession. [1](#0-0) [2](#0-1)  A participant can therefore reuse an observed encryption key and submit malformed ciphertext to obtain the ECDH result needed to decrypt the earlier message. [3](#0-2) [4](#0-3) 

### Finding Description
`EncryptedMessage::read` accepts an attacker-controlled ephemeral `key`, Schnorr PoP, and raw encrypted message without authenticating them during parsing. [5](#0-4)  `Encryption::decrypt` only queues the PoP for later batch verification, then calculates the ECDH point and decrypts the message. [6](#0-5) [7](#0-6)  It immediately constructs an `EncryptionKeyProof` containing that ECDH point and a DLEq proof that it is the decryption key for the supplied `msg.key`. [8](#0-7) 

`calculate_share` then calls `C::F::from_repr` and returns `PedPoPError::InvalidShare` with `blame: Some(blame)` if the decrypted plaintext is non-canonical. [1](#0-0)  Because this is an early `?` return, execution never reaches `batch.verify_with_vartime_blame`, where the invalid PoP would otherwise be detected. [9](#0-8)  The code’s own threat model identifies this exact pattern—reusing another message’s key and causing blame to reveal the ECDH result—as the reason the PoP is required. [10](#0-9) 

### Impact Explanation
If an attacker observes an `EncryptedMessage` sent to a victim, they can reuse its ephemeral `key`, attach an invalid PoP, and provide ciphertext which decrypts to a non-canonical scalar. [11](#0-10) [12](#0-11)  The returned blame proof exposes `victim_secret * reused_key`, which is transformed through the transcript into the ChaCha20 key used to decrypt that earlier ciphertext. [13](#0-12) [14](#0-13)  If the earlier ciphertext contained a PedPoP secret share, the attacker recovers that participant’s contribution to the victim’s threshold secret share. [15](#0-14) [16](#0-15) 

### Likelihood Explanation
Any unauthenticated party able to supply a purported share to `calculate_share` can trigger this path with a malformed `EncryptedMessage`. [17](#0-16)  For scalar encodings whose modulus is substantially below the encoding space, such as Ristretto’s 32-byte encoding of an approximately `2^252` field, an arbitrary decrypted payload is overwhelmingly non-canonical. [12](#0-11) [1](#0-0)  The exploit requires a previously observed ciphertext addressed to the same victim and a blame/error path which exposes the returned `EncryptionKeyProof`. [18](#0-17) [19](#0-18) 

### Recommendation
Verify `msg.pop` before applying the cipher or before returning an `EncryptionKeyProof`. [20](#0-19)  Alternatively, split decryption into an authentication phase and a key-disclosure phase so malformed plaintext cannot return blame until the queued PoP batch has succeeded. [1](#0-0) [19](#0-18)  Messages with an invalid PoP should produce `blame: None`, since they are not authenticated as originating from the accused participant. [19](#0-18) 

### Proof of Concept
The following pseudocode shows the attacker-controlled byte layout consumed by `EncryptedMessage::read`: ephemeral key, `R`, `s`, then raw ciphertext. [11](#0-10) [21](#0-20) 

```rust
// `victim` is running `KeyMachine<Ristretto>::calculate_share`.
// `honest_msg` is a prior EncryptedMessage addressed to the victim.
let reused_key = honest_msg.key.to_bytes();

// The PoP is deliberately invalid. Canonical identity/zero encodings are sufficient
// because read only parses and canonicalizes them.
let invalid_pop_r = RistrettoPoint::identity().to_bytes();
let invalid_pop_s = Scalar::ZERO.to_repr();

// Fixed-width SecretShare bytes. For Ristretto, almost every arbitrary
// 32-byte decrypted value is non-canonical.
let ciphertext = [0xff; 32];

let mut wire = reused_key.as_ref().to_vec();
wire.extend(invalid_pop_r.as_ref());
wire.extend(invalid_pop_s.as_ref());
wire.extend(ciphertext);

let msg = EncryptedMessage::<Ristretto, SecretShare<Scalar>>::read(
  &mut wire.as_slice(),
  params,
).unwrap();

let mut shares = HashMap::new();
shares.insert(attacker_participant, msg);

match victim_key_machine.calculate_share(&mut rng, shares) {
  Err(PedPoPError::InvalidShare { blame: Some(proof), .. }) => {
    // proof.key is victim_enc_secret * reused_key.
    // Serialize/read the proof and feed proof.key to cipher(context, ...)
    // to decrypt honest_msg's SecretShare ciphertext.
  }
  _ => {}
}
```

The invalid PoP is queued but never evaluated before the non-canonical-share early return, so the proof is still emitted. [20](#0-19) [1](#0-0)  This bypasses the protection explicitly intended to stop key reuse from turning blame proofs into disclosure of earlier encrypted shares. [10](#0-9)

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L263-268)
```rust
impl<F: PrimeField> ReadWrite for SecretShare<F> {
  fn read<R: Read>(reader: &mut R, _: ThresholdParams) -> io::Result<Self> {
    let mut repr = F::Repr::default();
    reader.read_exact(repr.as_mut())?;
    Ok(SecretShare(repr))
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L366-377)
```rust
      let mut share = polynomial(&self.coefficients, l);
      let share_bytes = Zeroizing::new(SecretShare::<C::F>(share.to_repr()));
      share.zeroize();
      res.insert(l, self.encryption.encrypt(rng, l, share_bytes));
    }

    // Calculate our own share
    let share = polynomial(&self.coefficients, self.params.i());
    self.coefficients.zeroize();

    Ok((
      KeyMachine { params: self.params, secret: share, commitments, encryption: self.encryption },
```

**File:** crypto/dkg/pedpop/src/lib.rs (L463-478)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L479-485)
```rust
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
      share_bytes.zeroize();
      *self.secret += share.deref();

```

**File:** crypto/dkg/pedpop/src/lib.rs (L487-499)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L84-90)
```rust
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-118)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L123-131)
```rust
  // Use a static IV in acknowledgement of this
  let mut iv = Cc20Iv::default();
  // The \0 is to satisfy the length requirement (12), not to be null terminated
  iv.copy_from_slice(b"DKG IV v0.2\0");

  // ChaCha20 has the same commentary as the transcript regarding ZAlloc
  // TODO: https://github.com/serai-dex/serai/issues/151
  let res = ChaCha20::new(&key, &iv);
  zeroize(key.as_mut());
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-176)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L267-274)
```rust
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.key.to_bytes().as_ref())?;
    self.dleq.write(writer)
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-498)
```rust
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
```

**File:** crypto/schnorr/src/lib.rs (L49-53)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```
