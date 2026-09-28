### Title
Attacker-controlled bytes to `EncryptedMessage::read` forge the proof-of-possession via identity encryption key, injecting attacker-chosen plaintext shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CVE-2024-40545 (a crafted uploaded file is accepted and "executed"), `EncryptedMessage::read` accepts fully attacker-controlled bytes and the resulting object is "executed" by `Decryption::decrypt_with_proof` / `Encryption::decrypt`. Because deserialization uses `Ciphersuite::read_G` — which enforces canonicality but does **not** reject the identity point — an attacker can set `key = identity`, making the Schnorr PoP trivially forgeable and the ECDH shared secret a publicly known constant (the identity encoding). The ciphertext then decrypts to attacker-chosen plaintext delivered as a legitimate encrypted message.

### Finding Description
- `EncryptedMessage::read` reads `key` via `C::read_G` and `pop` via `SchnorrSignature::read`, both of which only check canonical encodings, not identity [1](#0-0) [2](#0-1) [3](#0-2) 
- Contrast with `Curve::read_G` in FROST, which explicitly rejects identity — showing the codebase recognizes identity is dangerous for key/nonce material, but `EncryptionKeyMessage`/`EncryptedMessage` use the ciphersuite-level reader [4](#0-3) 
- The PoP check is `R + c·A − s·G == 0` via `batch_statements`; with `A = key = identity`, any `(R = s·G, s)` satisfies it — no knowledge of a discrete log is required [5](#0-4) [6](#0-5) 
- `decrypt` computes `ecdh(enc_key_priv, msg.key)`; with `msg.key = identity` the shared point is identity, so `cipher()` derives a ChaCha20 key from the constant identity encoding and a fixed IV — a key any attacker can compute [7](#0-6) [8](#0-7) 
- The comment at lines 83–90 claims the PoP prevents exactly this class of abuse (attaching foreign/malformed keys); the identity point bypasses it entirely.

### Impact Explanation
Any unprivileged party able to feed bytes to `EncryptedMessage::read` (a public DKG/encrypted-share message) can craft a message that (a) passes PoP verification despite the sender knowing no secret key, and (b) decrypts under a publicly known key to a fully attacker-chosen plaintext. Where the plaintext is a secret share, the attacker dictates the exact share value the victim records — a forged-proof / protocol-injection primitive equivalent to the "crafted file executed" class, and strictly stronger than an honest-but-malicious sender who can only choose a share value, not bypass the possession proof and key-binding the design relies on for blame proofs.

### Likelihood Explanation
Reachable wherever serialized `EncryptedMessage`s are accepted from peers and passed through `read` → `decrypt`/`decrypt_with_proof`. The forgery requires only picking `s`, setting `R = s·G`, `key = identity`, and encrypting the desired plaintext under the known keystream — fully deterministic, no brute force. Exploitable impact on share integrity is bounded by downstream share-verification checks in the DKG, but the PoP bypass and key-substitution itself is unconditional.

### Recommendation
Reject identity points in `EncryptedMessage::read`, `EncryptionKeyMessage::read`, and `EncryptionKeyProof::read` (and in `SchnorrSignature::read` for `R`), matching `Curve::read_G`'s identity rejection in `crypto/frost/src/curve/mod.rs`. Additionally, have `cipher()`/`ecdh()` refuse the identity shared secret rather than keying ChaCha20 from it.

### Proof of Concept
```text
1. Attacker serializes EncryptedMessage {
     key  = identity_point_bytes,          // canonical encoding of C::G::identity()
     pop  = SchnorrSignature { R = s*G, s } // any s; verifies since A = identity:
                                             // R + c*I - s*G = R - s*G = 0
     msg  = ChaCha20(cipher(context, I_bytes), iv="DKG IV v0.2\0")
              XOR chosen_plaintext_share    // keystream is publicly computable
   }
2. Victim calls EncryptedMessage::read -> succeeds (canonical encodings only).
3. decrypt(): pop.batch_verify passes (forgery above);
   ecdh(enc_priv, identity) = identity -> same keystream attacker used;
   msg.msg = chosen_plaintext_share, accepted as the sender's encrypted share.
4. decrypt_with_proof(): same pop passes; DLEq for [G, identity] -> [enc_pub, key]
   still verifies only if proof.key corresponds — the core flaw is step 3, where
   no proof is needed and the share is already consumed.
```

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-133)
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
  zeroize(key.as_mut());
  res
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-177)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-379)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L487-500)
```rust
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

**File:** crypto/schnorr/src/lib.rs (L51-53)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/lib.rs (L88-99)
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
```

**File:** crypto/frost/src/curve/mod.rs (L124-131)
```rust
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```
