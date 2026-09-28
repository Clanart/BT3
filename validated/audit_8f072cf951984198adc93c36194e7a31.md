### Title
Public DKG share recovery through attacker-controlled encryption key - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
PedPoP accepts any canonical point as a participant’s share-encryption public key, including points with a known discrete logarithm such as the identity. Shares encrypted to that participant use `ephemeral_key * enc_key`, so an identity encryption key reduces every ECDH result to the publicly known identity. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`EncryptionKeyMessage::read` deserializes `enc_key` with `C::read_G`, which checks canonical encoding but does not reject identity or require a proof of possession. [4](#0-3) [5](#0-4)   
A malicious participant can therefore register an encryption key `E = xG` for a known `x`, including `x = 0` and `E = identity`.

For each share sent to that participant, `encrypt` chooses an ephemeral scalar `k`, publishes `K = kG`, and derives the shared secret as `kE`. [6](#0-5)   
Because `K` is public, anyone who observes the encrypted message can independently compute `xK = xkG = kE`. For `E = identity`, the shared secret is always the identity.

The ChaCha20 key is derived only from the DKG context and serialized ECDH result, with a static IV. [7](#0-6)   
Consequently, the observer can reconstruct the cipher and directly recover the serialized secret share from `EncryptedMessage.msg`. [8](#0-7) 

### Impact Explanation
If encrypted PedPoP shares are relayed through a coordinator or otherwise visible to parties other than their intended recipient, an attacker-controlled participant can make all shares addressed to that participant publicly decryptable. [9](#0-8)   
Collecting the shares sent to that participant recovers that participant’s aggregate threshold key share. This violates PedPoP’s confidentiality assumption that ECDH protects secret shares in transit. [10](#0-9) 

### Likelihood Explanation
The attack requires only malformed commitment bytes supplied by the participant receiving the shares. No malformed scalar, invalid point, collision, protocol replay, or cryptographic assumption failure is required. [4](#0-3)   
The PoP on each `EncryptedMessage` binds the ephemeral key to the sender and ciphertext, but it does not prove knowledge of the receiving `enc_key`; it therefore does not prevent this attack. [11](#0-10) 

### Recommendation
Require a proof of possession for each participant’s registered `enc_key`, bound to the DKG context, participant index, and commitment message, before accepting `EncryptionKeyMessage`. Also reject the identity point for `enc_key` as a defense-in-depth measure. [12](#0-11) [13](#0-12) 

### Proof of Concept
1. A malicious participant sends an otherwise syntactically valid `EncryptionKeyMessage` whose final `enc_key` encoding is the canonical identity point.
2. `EncryptionKeyMessage::read` accepts it, and `Decryption::register` stores identity as that participant’s encryption key. [4](#0-3) [13](#0-12) 
3. Each honest sender encrypts that participant’s share with `ecdh(key, identity) = identity`. [2](#0-1) [14](#0-13) 
4. Anyone who obtains the resulting `EncryptedMessage` constructs `cipher(context, identity)` and XORs the keystream with `msg`, recovering the serialized `SecretShare`. [7](#0-6) [8](#0-7)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L48-59)
```rust
/// Wraps a message with a key to use for encryption in the future.
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub struct EncryptionKeyMessage<C: Ciphersuite, M: Message> {
  msg: M,
  enc_key: C::G,
}

// Doesn't impl ReadWrite so that doesn't need to be imported
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
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

**File:** crypto/dkg/pedpop/src/lib.rs (L222-226)
```rust
/// The secret share message, to be sent to the party it's intended for over an authenticated
/// channel.
///
/// If any participant sends multiple secret shares to another participant, they are faulty.
// This should presumably be written as SecretShare(Zeroizing<F::Repr>).
```

**File:** crypto/dkg/pedpop/src/lib.rs (L357-370)
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
```
