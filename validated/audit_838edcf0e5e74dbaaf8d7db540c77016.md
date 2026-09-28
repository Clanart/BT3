### Title
PedPoP accepts the identity point as a DKG encryption key, collapsing ECDH to a publicly-known shared secret - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
The russh advisory (GHSA-cqvm-j2r2-hwpg) is about accepting degenerate Diffie-Hellman public values (`e = 1`) without validation, which makes the negotiated shared secret a publicly-known constant. Serai's PedPoP DKG encryption has the same class of bug: a participant-supplied encryption key is deserialized via `C::read_G` with no identity check, and the ECDH scalar multiplication `enc_key * peer_pub` then evaluates to the identity element — a shared secret any observer can recompute.

### Finding Description
Participants broadcast an `EncryptionKeyMessage` whose `enc_key` field is read by `EncryptionKeyMessage::read` at `crypto/dkg/pedpop/src/encryption.rs:57-59` using only `C::read_G`, which enforces canonical encoding but explicitly permits the identity point (`crypto/ciphersuite/src/lib.rs:91-100` — it only checks `point.to_bytes() == encoding`). `Decryption::register` stores the key verbatim at `encryption.rs:351-362` with no non-identity check. When any honest dealer encrypts a share to that participant, `encrypt` calls `ecdh(&key, to)` (`encryption.rs:154`), which computes `to * ephemeral_scalar` (`encryption.rs:95-97`). With `to = identity`, the shared point is always `identity`, so `cipher(context, identity)` (`encryption.rs:101-133`) derives a ChaCha20 key that is a deterministic public function of the DKG context and the constant identity encoding. The stream cipher also uses a fixed IV `b"DKG IV v0.2\0"` (`encryption.rs:123-127`), which is safe only because ECDH keys are assumed unique per message — under an identity peer key, the same keystream is reused for every dealer's share to that participant.

The same missing check applies to `EncryptedMessage.key` (`encryption.rs:173`): a sender can set the per-message ephemeral key to identity, again producing `ecdh = identity` on decryption at `encryption.rs:487`.

### Impact Explanation
Any participant who registers `enc_key = identity` causes all secret shares addressed to them — from every dealer in the PedPoP session — to be encrypted under a keystream any observer can regenerate. Since PedPoP share messages are broadcast through a common medium, an eavesdropper recovers the plaintext `SecretShare` of that participant from the transcript alone. This is direct recovery of a threshold secret share by an unprivileged third party, i.e., key share recovery, and additionally produces key+IV reuse across all shares destined to that participant. Analogously, a malicious sender using an identity per-message `key` makes their own share ciphertext publicly decryptable and can be combined with `blame` disclosures to strip the per-message confidentiality the PoP/ECDH construction is meant to preserve.

### Likelihood Explanation
An unprivileged DKG participant fully controls the `EncryptionKeyMessage` they broadcast; choosing the canonical identity encoding requires no special access and passes all existing checks. The exposure is unconditional — every honest dealer encrypting to that participant produces publicly-decryptable output. Exploitation only requires a passive observer of the DKG transcript. It does not by itself recover the full group secret (one participant's share is leaked), but it silently reduces the confidentiality of the threshold protocol below its stated assumptions, matching the Medium severity of the source advisory.

### Recommendation
Reject the identity element when accepting peer-controlled public keys: in `EncryptionKeyMessage::read`/`Decryption::register` (reject `enc_key.is_identity()`), and in `EncryptedMessage::read`/`decrypt`/`decrypt_with_proof` for `msg.key`. Also reject identity in `Commitments::read` (dkg/pedpop) and in FROST `NonceCommitments`/`GeneratorCommitments::read` (crypto/frost/src/nonce.rs:34-36), where identity nonce commitments are likewise currently accepted. As defense-in-depth, assert `!ecdh_result.is_identity()` inside `ecdh` before deriving the cipher.

### Proof of Concept
```rust
// Participant Mallory registers an identity encryption key.
let malicious = EncryptionKeyMessage::<C, _> {
    msg: their_dkg_msg,
    enc_key: C::G::identity(), // passes C::read_G canonical check
};

// Every honest dealer encrypts to it:
// encrypt(rng, context, from, to = identity, share)
//   -> ecdh(&ephemeral, identity) = identity
//   -> cipher(context, identity)  // key = H("DKG Encryption v0.2" || context || identity_bytes)

// An observer of the broadcast EncryptedMessage reconstructs the same cipher:
let mut t = RecommendedTranscript::new(b"DKG Encryption v0.2");
t.append_message(b"context", context);
t.domain_separate(b"encryption_key");
t.append_message(b"shared_key", C::G::identity().to_bytes());
let key = t.challenge(b"key");
let mut cc20 = ChaCha20::new(&key[..32].into(), b"DKG IV v0.2\0".into());
cc20.apply_keystream(&mut ciphertext); // recovers Mallory's plaintext secret share
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
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
```

**File:** crypto/frost/src/nonce.rs (L34-41)
```rust
  fn read<R: Read>(reader: &mut R) -> io::Result<GeneratorCommitments<C>> {
    Ok(GeneratorCommitments([<C as Curve>::read_G(reader)?, <C as Curve>::read_G(reader)?]))
  }

  fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.0[0].to_bytes().as_ref())?;
    writer.write_all(self.0[1].to_bytes().as_ref())
  }
```
