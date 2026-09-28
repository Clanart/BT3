### Title
Replayed encryption keys cause blame proofs to disclose unrelated DKG shares - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
PedPoP attempts to prevent an attacker from replaying another sender’s per-message encryption key by requiring a Schnorr proof-of-possession, but that proof is only queued for later batch verification. `Encryption::decrypt` derives the recipient’s ECDH key and constructs an `EncryptionKeyProof` before the proof-of-possession is verified. `KeyMachine::calculate_share` can then return that proof early when decrypted share bytes are non-canonical, allowing a malformed replayed-key message to force publication of the ECDH key needed to decrypt the original honest message.

### Finding Description
`EncryptedMessage::read` accepts attacker-controlled message bytes containing a public per-message key, Schnorr proof, and ciphertext without authenticating them. [1](#0-0) 

The comments explicitly identify the intended attack: an observer may reuse Alice’s per-message key `X`, cause Bob to reveal `bX`, and thereby decrypt Alice’s original message. [2](#0-1) 

However, `Encryption::decrypt` only queues `msg.pop` into a `BatchVerifier`; it then immediately computes `ecdh(self.enc_key, msg.key)`, decrypts the message, and returns an `EncryptionKeyProof` containing that ECDH result. [3](#0-2) 

`calculate_share` parses the decrypted bytes and returns `PedPoPError::InvalidShare` with `blame: Some(blame)` before calling `batch.verify_with_vartime_blame()`. [4](#0-3) 

The proof itself serializes the revealed ECDH point directly, so publishing the returned blame discloses the key needed to decrypt every message using the same per-message key. [5](#0-4) 

### Impact Explanation
A DKG participant or other party able to submit an encrypted share can replay an observed per-message key with an invalid proof-of-possession and a mutated ciphertext. If the resulting plaintext is a non-canonical scalar, the recipient emits a blame proof exposing the ECDH shared key. Publishing that blame decrypts the original encrypted DKG share associated with the replayed key.

This does not by itself recover the recipient’s complete threshold secret, but it violates PedPoP’s intended confidentiality and discloses a secret share contribution to all parties that observe the blame. The issue is therefore a concrete Medium-severity information-disclosure vulnerability.

### Likelihood Explanation
The attack requires submitting a share to a recipient and observing an encrypted message addressed to that recipient. Those prerequisites match the protocol’s expected untrusted share input path.

For fields with a substantial non-canonical encoding space, such as Ristretto’s scalar field, a randomly mutated ciphertext will usually deserialize to a non-canonical scalar. If it remains canonical, the invalid proof-of-possession is caught by batch verification and no blame proof is released; a subsequent protocol attempt can repeat the attack.

### Recommendation
Do not construct or return an `EncryptionKeyProof` until the message’s proof-of-possession has been verified. The simplest fix is for `Encryption::decrypt` to synchronously verify `msg.pop` before deriving the ECDH key or producing a blame proof. Alternatively, `calculate_share` must defer returning blame until the PoP batch has succeeded, and return `blame: None` for any message whose PoP fails.

### Proof of Concept
Conceptual attack against a Ristretto PedPoP instance:

```rust
// Alice sent Bob:
//   honest = EncryptedMessage { key: K, pop: valid_pop_A, msg: C_A }
//
// Eve observes `honest` and constructs wire bytes:
//   forged = K || arbitrary_valid_encoded_pop || mutated_C
//
// `mutated_C` is a same-length ciphertext chosen so that
// ChaCha20(decrypt(mutated_C)) is a non-canonical scalar with high probability.

let forged_msg =
  EncryptedMessage::<Ristretto, SecretShare<_>>::read(
    &mut forged_wire.as_slice(),
    params,
  )?;

let shares = HashMap::from([(eve_participant, forged_msg)]);
let err = bob_key_machine
  .calculate_share(&mut rng, shares)
  .unwrap_err();

// Because parsing fails before the queued PoP verification runs:
//   err == InvalidShare { participant: eve_participant, blame: Some(proof) }
//
// `proof.key == bob_enc_private * K`, the same ECDH key used for Alice's
// original ciphertext. Publishing `proof.serialize()` therefore lets observers
// derive the ChaCha20 key and decrypt Alice's original `C_A`.
```

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L83-90)
```rust
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L260-274)
```rust
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub struct EncryptionKeyProof<C: Ciphersuite> {
  key: Zeroizing<C::G>,
  dleq: DLEqProof<C::G>,
}

impl<C: Ciphersuite> EncryptionKeyProof<C> {
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.key.to_bytes().as_ref())?;
    self.dleq.write(writer)
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-498)
```rust
    msg.pop.batch_verify(
      rng,
      batch,
      batch_id,
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    );

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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L476-499)
```rust
    for (l, share_bytes) in shares.drain() {
      let (mut share_bytes, blame) =
        self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
      share_bytes.zeroize();
      *self.secret += share.deref();

      blames.insert(l, blame);
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
    }
    batch.verify_with_vartime_blame().map_err(|id| {
      let (l, blame) = match id {
        BatchId::Decryption(l) => (l, None),
        BatchId::Share(l) => (l, Some(blames.remove(&l).unwrap())),
      };
      PedPoPError::InvalidShare { participant: l, blame }
    })?;
```
