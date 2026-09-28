### Title
Unauthenticated DKG encryption key enables participant share recovery - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary

`EncryptionKeyMessage` transports a participant’s PedPoP commitments together with an encryption public key, but the Schnorr proof authenticates only the commitments and claimed `Participant` index—not `enc_key`. An attacker can therefore replay a victim’s valid commitments while substituting an encryption key they control, causing other participants to encrypt the victim’s DKG shares to the attacker. [1](#0-0) [2](#0-1) 

### Finding Description

`EncryptionKeyMessage::read` accepts an arbitrary curve point as `enc_key` immediately after deserializing the embedded message. [3](#0-2)  During round-one verification, `verify_r1` registers that key under the map’s claimed `Participant` before verifying only the commitments’ Schnorr proof. [4](#0-3)  The challenge commits to the context, participant index, nonce, and serialized commitments, but not to `enc_key`. [2](#0-1) 

Consequently, a substituted `enc_key` is stored in `Decryption::enc_keys` without proof that it belongs to, or was selected by, the claimed participant. [5](#0-4)  `generate_secret_shares` later encrypts each recipient’s scalar share to this registered key. [6](#0-5) [7](#0-6) 

### Impact Explanation

An attacker who supplies a victim-labelled `EncryptionKeyMessage` containing the victim’s valid `Commitments` and an attacker-controlled `enc_key` can learn every `SecretShare` that honest participants encrypt for that victim. Summing those decrypted shares yields the victim’s final threshold secret share, since `calculate_share` derives it by summing the individually encrypted shares. [8](#0-7)  This is recovery of a private threshold-key share from public protocol inputs, not merely a proof or attribution failure.

### Likelihood Explanation

The attack requires the application to associate an attacker-supplied serialized registration with the victim’s `Participant` key, such as by accepting a relayed map entry where the commitments are valid but the trailing encryption key is substituted. The vulnerable boundary is `EncryptionKeyMessage::read` followed by `SecretShareMachine::generate_secret_shares`; the cryptographic verification itself provides no binding that would reject the substitution. [9](#0-8) [10](#0-9) 

### Recommendation

Bind `enc_key` into the authenticated registration. Include `enc_key` in the signed/commitment transcript—for example, append it to `cached_msg` before calculating the Schnorr PoK challenge, or add a separate signature/proof over `context || participant || commitments || enc_key`. Verification must reject any registration whose encryption key differs from the authenticated key. [2](#0-1) [11](#0-10) 

### Proof of Concept

1. Victim `v` publishes a valid `EncryptionKeyMessage` containing `Commitments { commitments, cached_msg, sig }` and encryption key `E_v`.
2. Attacker generates `e_a` and `E_a = e_a * G`, then constructs `{ msg: victim_commitments, enc_key: E_a }` and submits it under map key `v`.
3. `EncryptionKeyMessage::read` accepts `E_a`, `verify_r1` registers `E_a` for `v`, and the victim’s commitments PoK verifies because `E_a` is absent from the challenge. [3](#0-2) [4](#0-3) 
4. For every sender `s`, `generate_secret_shares` computes `share_s(v)` and encrypts it under `E_a`. [12](#0-11) 
5. For each ciphertext `(K, msg)`, the attacker derives `K * e_a`, feeds it to `cipher`, and decrypts `share_s(v)`. The victim’s private share is `Σ_s share_s(v)`, matching the sum performed by `calculate_share`. [13](#0-12) [14](#0-13)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L48-64)
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

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    self.msg.write(writer)?;
    writer.write_all(self.enc_key.to_bytes().as_ref())
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-111)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L85-94)
```rust
#[allow(non_snake_case)]
fn challenge<C: Ciphersuite>(context: [u8; 32], l: Participant, R: &[u8], Am: &[u8]) -> C::F {
  let mut transcript = RecommendedTranscript::new(b"DKG PedPoP v0.2");
  transcript.domain_separate(b"schnorr_proof_of_knowledge");
  transcript.append_message(b"context", context);
  transcript.append_message(b"participant", l.to_bytes());
  transcript.append_message(b"nonce", R);
  transcript.append_message(b"commitments", Am);
  C::hash_to_F(b"DKG-PedPoP-proof_of_knowledge-0", &transcript.challenge(b"schnorr"))
}
```

**File:** crypto/dkg/pedpop/src/lib.rs (L163-184)
```rust
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L313-334)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L359-370)
```rust
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
