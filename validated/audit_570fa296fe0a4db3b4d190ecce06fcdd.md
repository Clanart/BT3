[1](#0-0) ### Title
Forged PedPoP encrypted shares can expose unrelated DKG secret shares - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` decrypts and interprets an encrypted share before verifying the sender’s proof of possession for the ephemeral encryption key. An invalid PoP is queued for batch verification, but a non-canonical decrypted scalar causes an early error containing an `EncryptionKeyProof` that reveals the ECDH shared point. [2](#0-1) [1](#0-0) 

### Finding Description
`EncryptedMessage::read` accepts three attacker-controlled components: an ephemeral group element, a Schnorr PoP, and encrypted share bytes. [3](#0-2) 

During `calculate_share`, `Encryption::decrypt` queues the PoP for later batch verification, computes `msg.key * recipient_enc_key`, decrypts the ciphertext, and immediately returns an `EncryptionKeyProof` containing that shared ECDH point. [4](#0-3) 

`calculate_share` then calls `C::F::from_repr` on the decrypted bytes and returns `InvalidShare { blame: Some(blame) }` before `batch.verify_with_vartime_blame()` is reached. [5](#0-4) 

The resulting proof is intended to reveal the ECDH point for the message being adjudicated: `decrypt_with_proof` verifies that `proof.key` corresponds to `msg.key` and the recipient encryption key, then uses `proof.key` to derive the ChaCha20 keystream and decrypt the message. [6](#0-5) 

This creates the exact confused-deputy scenario described by the code’s own warning: an observer can copy the ephemeral key from an honest encrypted share, send a forged message claiming that key, and cause the recipient to publish a proof revealing the honest message’s ECDH key. The PoP exists specifically to prevent this, but the early scalar-decoding path bypasses it. [7](#0-6) 

### Impact Explanation
A malicious participant can copy the ephemeral `key` field from another participant’s encrypted DKG share to a victim, attach an invalid PoP and ciphertext, and induce the victim to emit a blame proof exposing `victim_enc_key * copied_key`. That ECDH point decrypts the copied participant’s genuine encrypted share to the victim. [8](#0-7) [6](#0-5) 

Applying the attack against threshold-many recipients and copying the corresponding ephemeral keys from one honest sender yields enough decrypted PedPoP shares to reconstruct that sender’s polynomial secret. Repeating this approach can compromise key material contributing to the generated threshold key. [9](#0-8) [10](#0-9) 

### Likelihood Explanation
The forged fields only need canonical encodings: `read_F`/`read_G` enforce encoding validity, but do not authenticate the PoP or plaintext. [11](#0-10) [3](#0-2) 

Because the cipher is unauthenticated ChaCha20, an arbitrary ciphertext decrypts to effectively random scalar bytes; for commonly used 252-bit scalar fields, random 32-byte plaintext is non-canonical with high probability. The sender can therefore trigger the early `from_repr` failure without knowing the ECDH key. [12](#0-11) [13](#0-12) 

### Recommendation
Do not create or return an `EncryptionKeyProof` until the message’s PoP has been verified.

Conceptually:

1. Parse the `EncryptedMessage`, but do not decrypt it yet.
2. Verify its `pop` against `pop_challenge(context, pop.R, key, sender, ciphertext)`.
3. If the PoP fails, report `InvalidShare` with `blame: None` and never derive or publish an ECDH proof.
4. Only after PoP verification succeeds, derive the ECDH key, decrypt, canonicalize the scalar, and construct a blame proof for semantically invalid shares.

The processor path should also avoid serializing blame proofs produced before PoP validation. [14](#0-13) [15](#0-14) 

### Proof of Concept
Assume participant `A` sends victim `B` an encrypted share:

```text
honest = EncryptedMessage {
  key: K_A,
  pop: PoP_A,
  msg: C_A,
}
```

An untrusted participant `M` observes `K_A`’s encoding and submits its own forged share to `B`:

```text
forged = EncryptedMessage {
  key: K_A,                  // copied from A -> B
  pop: arbitrary_canonical, // invalid PoP under M
  msg: arbitrary_bytes,     // likely decrypts to a non-canonical scalar
}
```

The vulnerable execution is:

```rust
// B executes calculate_share.
let (plaintext, blame) =
  encryption.decrypt(rng, &mut batch, BatchId::Decryption(M), M, forged);

// PoP verification has only been queued, not performed.
// decrypt already computed and stored:
//   blame.key = B_enc_secret * K_A

// Random decrypted bytes are likely non-canonical.
let share = C::F::from_repr(plaintext.0)
  .ok_or(PedPoPError::InvalidShare {
    participant: M,
    blame: Some(blame), // exposes B_enc_secret * K_A
  })?;
```

Because `A` used the same `K_A` and `B`’s encryption key, the revealed point equals the ECDH shared point used to encrypt `C_A`. Anyone receiving the serialized blame proof can derive the `cipher(context, proof.key)` keystream and recover `A`’s secret share plaintext from `C_A`. [16](#0-15) [8](#0-7) [6](#0-5)

### Citations

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

**File:** crypto/dkg/pedpop/src/lib.rs (L476-499)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L83-90)
```rust
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-130)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}

// Each ecdh must be distinct. Reuse of an ecdh for multiple ciphers will cause the messages to be
// leaked.
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L151-165)
```rust
  // Generate a new key for this message, satisfying cipher's requirement of distinct keys per
  // message, and enabling revealing this message without revealing any others
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());

  let pub_key = C::generator() * key.deref();
  let nonce = Zeroizing::new(C::random_nonzero_F(rng));
  let pub_nonce = C::generator() * nonce.deref();
  EncryptedMessage {
    key: pub_key,
    pop: SchnorrSignature::sign(
      &key,
      nonce,
      pop_challenge::<C>(context, pub_nonce, pub_key, from, msg.deref().as_ref()),
    ),
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L469-499)
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
```

**File:** crypto/ciphersuite/src/lib.rs (L74-100)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }

  /// Read a canonical point from something implementing std::io::Read.
  ///
  /// The provided implementation is safe so long as `GroupEncoding::to_bytes` always returns a
  /// canonical serialization.
  #[cfg(any(feature = "alloc", feature = "std"))]
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```

**File:** processor/src/key_gen.rs (L419-425)
```rust
                PedPoPError::InvalidShare { participant, blame } => {
                  Err(ProcessorMessage::InvalidShare {
                    id,
                    accuser: params.i(),
                    faulty: participant,
                    blame: Some(blame.map(|blame| blame.serialize())).flatten(),
                  })?
```
