### Title
PedPoP `EncryptionKeyMessage::enc_key` accepts the identity point with no proof of possession, making ECDH-derived share encryption publicly decryptable - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
Like CVE-2022-46478 (deserialized input accepted without validation enabling unauthorized effect), `EncryptionKeyMessage::read` accepts an attacker-supplied `enc_key` group element with only canonical-encoding checks: `C::read_G` does not reject the identity point, and no proof of possession / knowledge is ever verified for `enc_key`. Registering `enc_key = identity` makes every ECDH shared key for messages encrypted to that participant equal to the identity point — a public constant — so the ChaCha20 keystream protecting secret shares is derivable by anyone who observes the ciphertext.

### Finding Description
`EncryptionKeyMessage::read` deserializes the encryption key via `C::read_G(reader)` and nothing else — no PoP, no identity rejection [1](#0-0) . The generic `Ciphersuite::read_G` rejects only invalid/non-canonical encodings; unlike `Curve::read_G` it permits the identity [2](#0-1) [3](#0-2) . `Decryption::register` stores this key verbatim, asserting only non-duplication [4](#0-3) . In `encrypt`, the shared secret is `ecdh(ephemeral_private, enc_key)`; when `enc_key` is identity, the product is always identity, and `cipher` derives the ChaCha20 key from `transcript(context, identity.to_bytes())` — entirely public inputs [5](#0-4) [6](#0-5) . Contrast with `EncryptedMessage`, which does carry a per-message PoP over `key` [7](#0-6)  — the registration key has no equivalent.

### Impact Explanation
Every `EncryptedMessage<C, SecretShare>` produced by honest participants for the malicious registrant is encrypted under a publicly derivable keystream. Any passive observer of the (authenticated but not confidential) channel can decrypt `f_sender(l)` for all honest senders — i.e., recover the DKG secret shares destined to participant `l`, violating the secrecy of honest participants' polynomial evaluations at index `l`. This is direct secret-share recovery from attacker-controlled deserialized bytes; it erodes the threshold's confidentiality margin (the recovered shares at index `l` are worth one full participant's worth of secret material to any eavesdropper, not just to `l`).

### Likelihood Explanation
Any party able to submit a commitments/`EncryptionKeyMessage` payload for a key-generation session (or anyone who can inject bytes reaching `EncryptionKeyMessage::read`) can register the identity encoding with a single crafted point; no computation, no collusion, no special position required. Identity encodings are trivial (`0x00..00`-style canonical encodings depending on curve), always parse, and nothing downstream rejects them.

### Recommendation
In `EncryptionKeyMessage::read`/`Decryption::register`, reject identity (`enc_key.is_identity()`) and require a Schnorr proof of possession over `enc_key` bound to `(context, participant)` — mirroring `challenge()`/`pop_challenge()` binding — before registering it in `Decryption::enc_keys`. Alternatively use a `Curve`-style `read_G` that rejects identity for all registration keys.

### Proof of Concept
1. Malicious participant `l` crafts `EncryptionKeyMessage` bytes: a valid `Commitments` payload followed by the canonical encoding of the identity point as `enc_key`.
2. Honest participants call `EncryptionKeyMessage::read` → `register(l, msg)`: accepted; `enc_keys[l] = identity`.
3. Each honest participant `h` calls `encrypt(rng, l, share_f_h(l))`: computes `ecdh = ephemeral * identity = identity`, keystream `k = ChaCha20(key = transcript(context || identity_bytes))`, ciphertext `ct = share ⊕ k`.
4. Any observer recomputes `k` from the public context and identity encoding, XORs `ct`, and recovers `f_h(l)` for every honest sender — a secret share recovered purely from attacker-supplied deserialized input.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-59)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L80-93)
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
}
```

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L151-156)
```rust
  // Generate a new key for this message, satisfying cipher's requirement of distinct keys per
  // message, and enabling revealing this message without revealing any others
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());

  let pub_key = C::generator() * key.deref();
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
