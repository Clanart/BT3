### Title
Identity public key accepted by `Ciphersuite::read_G` enables forged proof-of-possession signatures on `EncryptedMessage` - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptedMessage::read` and `EncryptionKeyMessage::read` deserialize the per-message ECDH key (`key`) and the registered encryption key (`enc_key`) via `C::read_G` — the base `Ciphersuite` implementation, which only enforces canonical encoding and explicitly permits the identity point [1](#0-0) . The stricter `Curve::read_G` that rejects identity exists but is only used inside FROST [2](#0-1) . The proof-of-possession is a plain `SchnorrSignature` verified as `s·G = R + c·A` [3](#0-2) . With `A = key = identity`, the equation reduces to `s·G = R`, which anyone satisfies by choosing `s` and setting `R = s·G` — the challenge `c` is fully determined by `(context, R, key, sender, msg)` and needs no secret [4](#0-3) .

### Finding Description
Like the Zabbix bug (an unsanitized request string interpreted as a file to execute), here untrusted wire bytes are interpreted as a proof-carrying public key with no sanitization of the degenerate identity case. `EncryptedMessage::read` reads `key` with `C::read_G(reader)?` with no identity rejection [5](#0-4) . The same omission exists for `enc_key` in `EncryptionKeyMessage::read` [6](#0-5) , and for the per-participant commitments in `Commitments::read`, whose PoK signature is verified against `commitments[0]` — again forgeable if it is identity [7](#0-6) . `pop`'s documented purpose is to prevent an attacker from claiming someone else's key and thereby weaponizing blame proofs; an identity key evades the intended possession check entirely.

### Impact Explanation
An unprivileged DKG participant can broadcast an `EncryptedMessage` whose PoP is a forged Schnorr signature under `key = identity`, and whose ciphertext decrypts to attacker-chosen plaintext: the victim computes `ecdh(enc_key_priv, identity) = identity` [8](#0-7) , yielding a publicly known ChaCha20 keystream [9](#0-8) . `Encryption::decrypt` applies the keystream unconditionally, only queuing the PoP for batch verification — a queue the forged signature passes [10](#0-9) . Concretely this breaks the PoP invariant the protocol relies on for safe blame attribution: a forged-PoP message produces a "valid" `EncryptionKeyProof` whose shared key is the identity point (the DLEq between `[G, identity]` and `[enc_pub_key, identity]` is honestly provable since `enc_priv · identity = identity`), so blame verification accepts a decryption that never corresponded to a real ECDH exchange [11](#0-10) . Share correctness is still enforced downstream against PedPoP commitments, so this does not yield an honest share — the verified signature/proof on attacker-chosen decrypted material, with the possession check bypassed, is itself the concrete forgery.

### Likelihood Explanation
Reachable by any participant in a PedPoP session via `EncryptedMessage::<C, SecretShare<C::F>>::read` on bytes they broadcast (e.g., processor `key_gen.rs` share parsing). Exploitation is deterministic — no probability assumptions — since the Schnorr forgery is trivial once the public key is the identity. Severity is bounded because share-vs-commitment verification still rejects a bogus share value, limiting the result to a forged-proof/misattribution primitive rather than key compromise; accordingly this is assessed Medium.

### Recommendation
Reject the identity point wherever a group element is used as a public key or ECDH counterparty in the DKG layer: check `res.is_identity()` in `EncryptedMessage::read`, `EncryptionKeyMessage::read`, and `Commitments::read` (mirroring `Curve::read_G`), and ideally reject identity `R` in `SchnorrSignature`/`DLEqProof` verification paths. Also reject `commitments[0] == identity` before verifying the PedPoP PoK.

### Proof of Concept
```rust
// Forged EncryptedMessage PoP under key = identity, for any victim with registered
// enc_pub_key. context, params, and `from` are the attacker's session values.
let id_point = C::G::identity();
let s = C::F::random(&mut OsRng);              // arbitrary
let R = C::generator() * s;
let msg: E = /* any bytes of the correct length */;
let c = pop_challenge::<C>(context, R, id_point, from, msg.as_ref());
// SchnorrSignature { R, s } satisfies s*G == R + c*identity == R
let forged = EncryptedMessage { key: id_point, pop: SchnorrSignature { R, s }, msg };
// forged passes pop verification in decrypt()/decrypt_with_proof, and
// ecdh(enc_key_priv, identity) == identity gives attacker-chosen keystream.
```

### Citations

**File:** crypto/ciphersuite/src/lib.rs (L91-101)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
  }
```

**File:** crypto/frost/src/curve/mod.rs (L123-131)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```

**File:** crypto/schnorr/src/lib.rs (L88-110)
```rust
  pub fn batch_statements(&self, public_key: C::G, challenge: C::F) -> [(C::F, C::G); 3] {
    // s = r + ca
    // sG == R + cA
    // R + cA - sG == 0
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
  }

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-59)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-97)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-133)
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
  res
}
```

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L366-397)
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
    } else {
      Err(DecryptionError::InvalidProof)
    }
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-485)
```rust
    msg.pop.batch_verify(
      rng,
      batch,
      batch_id,
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    );
```

**File:** crypto/dkg/pedpop/src/lib.rs (L109-128)
```rust
impl<C: Ciphersuite> ReadWrite for Commitments<C> {
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
  }
```
