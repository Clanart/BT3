### Title
Identity DKG encryption key makes secret-share ciphertexts publicly decryptable - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
PedPoP accepts a participant’s encryption public key without rejecting the identity point. Any share encrypted to that participant then uses the identity as the ECDH shared secret, making the ChaCha20 stream key derivable from only the public DKG context and the known identity encoding.

### Finding Description
`EncryptionKeyMessage::read` accepts `enc_key` using only `C::read_G`, which checks canonicality but does not reject identity. `Decryption::register` stores that attacker-supplied key unchanged, and `Encryption::encrypt` later uses it as the ECDH recipient. `ecdh` computes `public * private`; when `public` is identity, the result is always identity. `cipher` derives the ChaCha20 key solely from the public context and that shared-secret encoding, with a fixed IV. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

The vulnerable call path is `SecretShareMachine::verify_r1` registering the unvalidated key, followed by `generate_secret_shares` encrypting each polynomial share to it. For `t = 1`, `polynomial` returns the sole coefficient as every recipient’s share, so every ciphertext addressed to the malicious participant exposes that sender’s complete contribution to the group private key. [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

### Impact Explanation
For a `1-of-n` PedPoP deployment using a curve whose canonical identity encoding is accepted, one participant can cause all shares addressed to it to be encrypted under a publicly computable key. A passive observer of the authenticated DKG messages can decrypt every honest participant’s coefficient and sum them to recover the group private key. More generally, the affected messages lose the confidentiality that the PedPoP encryption layer is intended to provide.

### Likelihood Explanation
The attacker only needs to be a DKG participant and submit the identity point as `enc_key` in the first-round `EncryptionKeyMessage`. No proof of possession is required for that long-term encryption key, and registration does not reject identity. The issue is configuration-dependent: full group-key recovery is immediate for `t = 1`, while larger thresholds leak individual polynomial evaluations rather than the complete private key.

### Recommendation
Reject identity and other invalid/low-order encryption public keys in `EncryptionKeyMessage::read` or `Decryption::register`, and require a proof of possession bound to the DKG context and participant for `enc_key`. Prefer deriving each cipher key from a transcript that also binds the sender, recipient, message ephemeral key, and DKG session. Regression tests should cover an identity `enc_key`, identity ephemeral message key, and a `t = 1` DKG.

### Proof of Concept
1. Instantiate PedPoP over Ristretto with `t = 1` and a public `context`.
2. As participant `m`, send an `EncryptionKeyMessage` whose `enc_key` is the canonical Ristretto identity encoding (`32` zero bytes). `C::read_G` accepts it and `register` stores it.
3. Each honest participant calls `generate_secret_shares`; its share for `m` is encrypted through `encrypt(..., to = identity, ...)`, producing ECDH output `identity`.
4. Any observer reconstructs the ChaCha20 key by running the public `cipher(context, identity)` transcript derivation and decrypts every `EncryptedMessage` addressed to `m`.
5. Decode each plaintext `SecretShare` as a scalar. Because `t = 1`, each scalar is that participant’s complete group-secret contribution; sum all contributions to obtain the group private key.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-58)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L460-467)
```rust
  pub(crate) fn encrypt<R: RngCore + CryptoRng, E: Encryptable>(
    &self,
    rng: &mut R,
    participant: Participant,
    msg: Zeroizing<E>,
  ) -> EncryptedMessage<C, E> {
    encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L160-187)
```rust
    let t = usize::from(self.params.t());
    let mut coefficients = Vec::with_capacity(t);
    let mut commitments = Vec::with_capacity(t);
    let mut cached_msg = vec![];

    for i in 0 .. t {
      // Step 1: Generate t random values to form a polynomial with
      coefficients.push(Zeroizing::new(C::random_nonzero_F(&mut *rng)));
      // Step 3: Generate public commitments
      commitments.push(C::generator() * coefficients[i].deref());
      cached_msg.extend(commitments[i].to_bytes().as_ref());
    }

    // Step 2: Provide a proof of knowledge
    let r = Zeroizing::new(C::random_nonzero_F(rng));
    let nonce = C::generator() * r.deref();
    let sig = SchnorrSignature::<C>::sign(
      &coefficients[0],
      // This could be deterministic as the PoK is a singleton never opened up to cooperative
      // discussion
      // There's no reason to spend the time and effort to make this deterministic besides a
      // general obsession with canonicity and determinism though
      r,
      challenge::<C>(self.context, self.params.i(), nonce.to_bytes().as_ref(), &cached_msg),
    );

    // Additionally create an encryption mechanism to protect the secret shares
    let encryption = Encryption::new(self.context, self.params.i(), rng);
```

**File:** crypto/dkg/pedpop/src/lib.rs (L205-220)
```rust
fn polynomial<F: PrimeField + Zeroize>(
  coefficients: &[Zeroizing<F>],
  l: Participant,
) -> Zeroizing<F> {
  let l = F::from(u64::from(u16::from(l)));
  // This should never be reached since Participant is explicitly non-zero
  assert!(l != F::ZERO, "zero participant passed to polynomial");
  let mut share = Zeroizing::new(F::ZERO);
  for (idx, coefficient) in coefficients.iter().rev().enumerate() {
    *share += coefficient.deref();
    if idx != (coefficients.len() - 1) {
      *share *= l;
    }
  }
  share
}
```

**File:** crypto/dkg/pedpop/src/lib.rs (L311-336)
```rust
    let mut batch = BatchVerifier::<Participant, C::G>::new(commitment_msgs.len());
    let mut commitments = HashMap::new();
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;

    commitments.insert(self.params.i(), self.our_commitments.drain(..).collect());
```

**File:** crypto/dkg/pedpop/src/lib.rs (L357-377)
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
    }

    // Calculate our own share
    let share = polynomial(&self.coefficients, self.params.i());
    self.coefficients.zeroize();

    Ok((
      KeyMachine { params: self.params, secret: share, commitments, encryption: self.encryption },
```
