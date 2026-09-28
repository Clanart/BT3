### Title

PedPoP accepts identity encryption keys, exposing encrypted DKG shares - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary

`EncryptionKeyMessage::read` accepts any canonical group element as a participant’s encryption key without rejecting the identity element, and PedPoP registers that key without validation. [1](#0-0) [2](#0-1) 

When the key is identity, every ECDH result used to encrypt shares to that participant is also identity, making the ChaCha20 key publicly reproducible. [3](#0-2) [4](#0-3) 

### Finding Description

`EncryptionKeyMessage<C, M>::read` parses `enc_key` via `C::read_G`, which performs canonical decoding but does not reject identity. [1](#0-0) [5](#0-4) 

`SecretShareMachine::verify_r1` stores this key in the encryption registry before validating only the unrelated proof of knowledge over the first Pedersen commitment. [6](#0-5) 

Subsequently, `encrypt` computes `ecdh = to * key`; if `to` is identity, the result is deterministically identity regardless of the random per-message scalar. [7](#0-6) [8](#0-7) 

The cipher transcript commits to that shared point, so anyone who knows the protocol context can derive the same keystream and decrypt the transmitted `SecretShare`. [9](#0-8) 

### Impact Explanation

An unprivileged participant can submit a malformed commitment message containing the identity encryption key and cause every other participant to encrypt that participant’s DKG shares under a publicly known ECDH secret. [10](#0-9) 

If the authenticated delivery channel is observable, the observer can recover the victim shares intended for the malicious participant, defeating PedPoP’s confidentiality layer and enabling recovery of that participant’s final secret share. [11](#0-10) [12](#0-11) 

This is particularly relevant because the protocol explicitly encrypts shares so communication channels do not expose them. [13](#0-12) 

### Likelihood Explanation

The attacker only needs to provide a participant commitment message accepted by `EncryptionKeyMessage::read`; no discrete logarithm, collision, malformed encoding, or special privilege is required. [14](#0-13) 

Replacing the trailing encryption-key encoding with the canonical encoding of `C::G::identity()` is sufficient because no later validation checks whether the registered encryption key is identity. [15](#0-14) 

### Recommendation

Reject identity and any other low-order/non-prime-subgroup points when reading or registering an `EncryptionKeyMessage`, preferably by requiring a nonzero public key before storing it in `Decryption::enc_keys`. [16](#0-15) 

Additionally, require a proof of possession for the long-term encryption key or otherwise bind it to the participant’s validated commitment message so malformed replacement keys cannot be introduced after the commitment PoK. [17](#0-16) 

### Proof of Concept

```rust
use ciphersuite::group::GroupEncoding;
use dkg::{Participant, ThresholdParams};
use pedpop::{Commitments, EncryptionKeyMessage};
use zeroize::Zeroizing;

// Victim's parameters and the participant index controlled by the attacker.
let params: ThresholdParams = ...;
let attacker: Participant = ...;

// Start from any syntactically valid attacker EncryptionKeyMessage.
let mut encoded = honest_commitment_message.serialize();

// Replace its trailing `enc_key` field with the canonical identity encoding.
let enc_len = <C::G as GroupEncoding>::Repr::default().as_ref().len();
encoded[encoded.len() - enc_len ..]
  .copy_from_slice(C::G::identity().to_bytes().as_ref());

// Serai accepts this message because only canonical point decoding is enforced.
let malicious =
  EncryptionKeyMessage::<C, Commitments<C>>::read(&mut encoded.as_slice(), params)
    .unwrap();

// Victims register `C::G::identity()` as the attacker's encryption key.
// Each resulting EncryptedMessage uses ECDH = identity * random_key = identity.
// An observer derives the ChaCha20 keystream from the protocol context and the
// canonical identity encoding, then XORs the ciphertext's `SecretShare` bytes to
// recover the plaintext share.
```

The critical difference from a normal malicious participant is that the malformed key also exposes the encrypted shares to third-party observers rather than only to the participant authorized to receive them. [18](#0-17)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-64)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    self.msg.write(writer)?;
    writer.write_all(self.enc_key.to_bytes().as_ref())
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L135-165)
```rust
fn encrypt<R: RngCore + CryptoRng, C: Ciphersuite, E: Encryptable>(
  rng: &mut R,
  context: [u8; 32],
  from: Participant,
  to: C::G,
  mut msg: Zeroizing<E>,
) -> EncryptedMessage<C, E> {
  /*
  The following code could be used to replace the requirement on an RNG here.
  It's just currently not an issue to require taking in an RNG here.
  let last = self.last_enc_key.to_bytes();
  self.last_enc_key = C::hash_to_F(b"encryption_base", last.as_ref());
  let key = C::hash_to_F(b"encryption_key", last.as_ref());
  last.as_mut().zeroize();
  */

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L340-361)
```rust
// A simple box for managing decryption.
#[derive(Clone, Debug)]
pub(crate) struct Decryption<C: Ciphersuite> {
  context: [u8; 32],
  enc_keys: HashMap<Participant, C::G>,
}

impl<C: Ciphersuite> Decryption<C> {
  pub(crate) fn new(context: [u8; 32]) -> Self {
    Self { context, enc_keys: HashMap::new() }
  }
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L487-499)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L313-337)
```rust
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
    Ok(commitments)
```

**File:** crypto/dkg/pedpop/src/lib.rs (L351-370)
```rust
  ) -> Result<
    (KeyMachine<C>, HashMap<Participant, EncryptedMessage<C, SecretShare<C::F>>>),
    PedPoPError<C>,
  > {
    let commitments = self.verify_r1(&mut *rng, commitments)?;

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

**File:** crypto/dkg/pedpop/src/lib.rs (L474-485)
```rust
    let mut batch = BatchVerifier::new(shares.len());
    let mut blames = HashMap::new();
    for (l, share_bytes) in shares.drain() {
      let (mut share_bytes, blame) =
        self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
      share_bytes.zeroize();
      *self.secret += share.deref();

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

**File:** spec/cryptography/Distributed Key Generation.md (L11-17)
```markdown
In order to protect the secret shares during communication, the `dkg` library
establishes a public key for encryption at the start of a given protocol.
Every encrypted message (such as the secret shares) then includes a per-message
encryption key. These two keys are used in an Elliptic-curve Diffie-Hellman
handshake to derive a shared key. This shared key is then hashed to obtain a key
and IV for use in a ChaCha20 stream cipher instance, which is xor'd against a
message to encrypt it.
```
