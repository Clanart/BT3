### Title
PedPoP accepts an identity encryption key at deserialization, making every share encrypted to that participant publicly decryptable - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptionKeyMessage::read` and `EncryptedMessage::read` parse group elements with `Ciphersuite::read_G`, which only enforces canonical encodings and does not reject the identity point (identity rejection exists only in `Curve::read_G`, used by FROST). A participant who registers `enc_key = identity` causes all shares encrypted to them to use a publicly known ECDH shared point, so anyone who observes the ciphertext (which is gossiped/published through the coordinator and later re-read during blame) can decrypt the secret share. This is the deserialization-class analog: untrusted external input is decoded without excluding a dangerous entity value, and that entity is then used to expose protected data.

### Finding Description
- `Ciphersuite::read_G` checks only `from_bytes` success and canonicality; the identity point is a valid, canonical, torsion-free group element and is accepted [1](#0-0) . For dalek-ff-group points, `from_bytes` accepts the identity (only torsion is rejected) [2](#0-1) .
- `Curve::read_G` exists precisely to reject identity, but PedPoP does not use it [3](#0-2) .
- `EncryptionKeyMessage::read` stores the attacker-supplied `enc_key` unchecked [4](#0-3) , and `Decryption::register` inserts it without validation [5](#0-4) .
- `encrypt` computes the shared key as `ecdh(ephemeral_private, to)` where `to` is the registered key. If `to` is identity, `ecdh` returns `identity` — a constant known to everyone [6](#0-5) .
- `cipher` derives the ChaCha20 key solely from `context || identity` (with a fixed IV), so the keystream is publicly computable [7](#0-6) .
- The `pop` signature does not help: it proves knowledge of the discrete log of `msg.key`, not of `enc_key`, and `msg.key` is freshly generated per message.

### Impact Explanation
An unprivileged participant in the DKG broadcasts an `EncryptionKeyMessage` whose `enc_key` is the identity point. Every other honest participant then encrypts their `SecretShare` for that participant under a keystream any third party can reproduce. Since `EncryptedMessage` ciphertexts are distributed through the coordinator/tributary and are also re-read in `VerifyBlame`, an eavesdropper (or a malicious participant who registered multiple identity-keyed indexes) recovers secret shares for participants they were never entitled to see, enabling threshold secret reconstruction without cooperation — a key-share recovery impact analogous to the report's "include arbitrary files/external data" effect.

### Likelihood Explanation
The attack requires only publishing a malformed commitment message during key generation — a normal, reachable input path. No collusion, timing, or brute force is needed; one crafted `enc_key` point suffices per victim index. It is mitigated only if the transport never exposes ciphertexts to parties other than the intended recipient, which is not guaranteed by the protocol design (blame explicitly republishes them).

### Recommendation
Reject the identity point (and ideally small-order/degenerate values) in `EncryptionKeyMessage::read` and `EncryptedMessage::read`/`EncryptionKeyProof::read` — e.g., use the identity-checking `Curve::read_G` or add an explicit `is_identity` check — so attacker-controlled "external entities" in the form of degenerate group elements cannot poison the ECDH domain.

### Proof of Concept
```rust
// crypto/dkg/pedpop — conceptual PoC
// Attacker (participant j) broadcasts an EncryptionKeyMessage with enc_key = identity:
let mut msg = vec![];
msg.extend(C::G::identity().to_bytes().as_ref()); // enc_key = identity
let ekm = EncryptionKeyMessage::<C, Commitments<C>>::read(&mut msg.as_slice(), params).unwrap();
// Accepted: C::read_G does not reject identity.
enc.register(j, ekm);

// Honest participant i encrypts a share to j:
let em: EncryptedMessage<C, SecretShare<C::F>> = enc.encrypt(&mut rng, j, share);
// Inside encrypt: ecdh(ephemeral, identity) == identity
// Anyone holding `em.serialize()` can now do:
let shared = C::G::identity(); // publicly known
let mut cipher = cipher::<C>(context, &Zeroizing::new(shared));
// cipher.apply_keystream(em.msg bytes) recovers SecretShare<C::F> in the clear.
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

**File:** crypto/dalek-ff-group/src/lib.rs (L429-437)
```rust
      fn from_bytes(bytes: &Self::Repr) -> CtOption<Self> {
        let decompressed = $DCompressed(*bytes).decompress();
        // TODO: Same note on unwrap_or as above
        let point = decompressed.unwrap_or($DPoint::identity());
        CtOption::new(
          $Point(point),
          choice(black_box(decompressed).is_some()) & choice($torsion_free(point)),
        )
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-132)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-362)
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
  }
```
