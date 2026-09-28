### Title
Attacker-controlled identity point in `EncryptedMessage::key` bypasses the proof-of-possession restriction and yields a fully known ECDH key, enabling injection of a chosen/known DKG secret share - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CVE-2016-7076 — where a security restriction (`noexec`) was silently bypassed by feeding attacker-controlled input through a privileged path — PedPoP's DKG encryption layer accepts the identity point as a per-message encryption key. `EncryptedMessage::read` reads `key` with `Ciphersuite::read_G`, which enforces canonical encoding but does **not** reject the identity element. The Schnorr proof-of-possession check that is supposed to prove the sender knows the key's discrete log is vacuously satisfiable for the identity key, and the resulting ECDH shared point is the identity, which the attacker fully controls. The "only someone who knows `k` in `k·G` can produce a decryptable message" restriction is bypassed entirely with untrusted bytes.

### Finding Description
`EncryptedMessage::read` uses `C::read_G` (the `Ciphersuite` trait method) for `key` [1](#0-0) . `Ciphersuite::read_G` only enforces a canonical encoding via a `to_bytes` round-trip; it permits the identity point [2](#0-1) . The identity-rejecting variant `Curve::read_G` exists in FROST but is not used here [3](#0-2) .

During `decrypt`, the PoP is verified as `msg.pop.verify(msg.key, pop_challenge(...))` [4](#0-3) . Schnorr verification reduces to `R + c·A − s·G == 0` [5](#0-4) ; with `A = key = identity`, any `s` with `R = s·G` satisfies it — no knowledge of a discrete log is needed, so the PoP restriction is bypassed.

Decryption then computes `ecdh = enc_key * msg.key = enc_key * identity = identity` [6](#0-5)  and derives the ChaCha20 keystream from `transcript(context || "encryption_key" || identity.to_bytes())` [7](#0-6) . Both sides of that derivation are public inputs, so the attacker knows the exact keystream and can make the victim decrypt a fully chosen plaintext.

### Impact Explanation
`EncryptedMessage` carries DKG secret shares (the code's test helpers explicitly assume `E` is a serialized secret share) [8](#0-7) . A malicious DKG participant can therefore deliver a share whose value they chose/know to a victim, or simply a known value the victim treats as a valid Pedersen-share payload. The victim's `ThresholdKeys` are built on a secret the attacker knows, collapsing the threshold scheme's confidentiality for that node's share and enabling key-share recovery of whatever is later derived. Additionally, because `key = identity` makes `ecdh = identity`, the "per-message distinct ECDH" invariant the cipher relies on ("reuse of an ecdh for multiple ciphers will cause the messages to be leaked") is weaponizable: every identity-keyed message to any victim uses the same known keystream [9](#0-8) .

### Likelihood Explanation
Reachable by any unprivileged DKG participant: they just send crafted bytes that flow through `EncryptedMessage::read` (an explicitly in-scope untrusted-byte sink) into `decrypt`. No collusion, broken BFT, or leaked keys are required — only the ability to send a DKG message. The only mitigating factor is that downstream share verification against the sender's committed polynomial may reject a malicious share value, but the PoP/ECDH restriction bypass itself is unconditional and still leaks keystream reuse across victims.

### Recommendation
Reject the identity point when reading `EncryptedMessage::key` — either call a `read_G` variant that checks `is_identity` (as `Curve::read_G` does) or explicitly assert `!msg.key.is_identity()` in `decrypt` before the ECDH. For belt-and-suspenders, also assert the ECDH result `ecdh(private, public)` is non-identity before keying the cipher.

### Proof of Concept
```rust
// Attacker sends EncryptedMessage { key: identity, pop: forged, msg: chosen_ciphertext }
// where msg = chosen_share_bytes XOR keystream(known).

let identity = C::G::identity();
// Forge PoP under public key = identity:
// verify needs R + c*identity - s*G == 0  =>  pick any s, set R = s*G
let s = C::F::random(rng);
let R = C::generator() * s;
let c = pop_challenge::<C>(context, R, identity, from, chosen_ct);
let pop = SchnorrSignature { R, s }; // verifies since c*identity = identity

// Decryption at victim:
// ecdh = enc_key * identity = identity
// keystream = ChaCha20(key = H(context || identity_bytes), iv = "DKG IV v0.2\0")
// attacker computes the same keystream => chosen_ct decrypts to attacker-chosen share
// EncryptedMessage::read accepted identity because Ciphersuite::read_G only checks
// canonical encoding, never is_identity.
```

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-97)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L99-133)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L217-256)
```rust
  // Assumes the encrypted message is a secret share.
  #[cfg(test)]
  pub(crate) fn invalidate_share_serialization<R: RngCore + CryptoRng>(
    &mut self,
    rng: &mut R,
    context: [u8; 32],
    from: Participant,
    to: C::G,
  ) {
    use ciphersuite::group::ff::PrimeField;

    let mut repr = <C::F as PrimeField>::Repr::default();
    for b in repr.as_mut() {
      *b = 255;
    }
    // Tries to guarantee the above assumption.
    assert_eq!(repr.as_ref().len(), self.msg.as_ref().len());
    // Checks that this isn't over a field where this is somehow valid
    assert!(!bool::from(C::F::from_repr(repr).is_some()));

    self.msg.as_mut().as_mut().copy_from_slice(repr.as_ref());
    *self = encrypt(rng, context, from, to, self.msg.clone());
  }

  // Assumes the encrypted message is a secret share.
  #[cfg(test)]
  pub(crate) fn invalidate_share_value<R: RngCore + CryptoRng>(
    &mut self,
    rng: &mut R,
    context: [u8; 32],
    from: Participant,
    to: C::G,
  ) {
    use ciphersuite::group::ff::PrimeField;

    // Assumes the share isn't randomly 1
    let repr = C::F::ONE.to_repr();
    self.msg.as_mut().as_mut().copy_from_slice(repr.as_ref());
    *self = encrypt(rng, context, from, to, self.msg.clone());
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

**File:** crypto/schnorr/src/lib.rs (L88-100)
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
```
