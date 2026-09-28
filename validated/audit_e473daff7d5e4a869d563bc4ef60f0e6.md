### Title
Malformed PedPoP share leaks another participant’s ECDH key and secret share before validating PoP - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` returns an `EncryptionKeyProof` immediately when the decrypted share is not a canonical scalar, before verifying the sender’s queued Schnorr proof-of-possession. An attacker can reuse the per-message key from a victim’s encrypted share, submit a malformed message with an invalid PoP, and—with high probability on Ristretto—cause the recipient to emit a blame proof containing `recipient_private * reused_key`. That shared key decrypts the original victim message and reveals its secret share. [1](#0-0) [2](#0-1) 

### Finding Description
`EncryptedMessage` carries an encryption public key, a Schnorr PoP, and the encrypted message bytes. The PoP specifically prevents an observer from reusing another message’s key, because reuse would make the recipient reveal the corresponding ECDH key in a blame proof. [3](#0-2) 

`SecretShare::read` accepts any fixed-width field representation without checking scalar canonicity, so arbitrary ciphertext bytes can be decoded into an `EncryptedMessage`. [4](#0-3) 

During `calculate_share`, `Encryption::decrypt` queues the PoP for later batch verification, computes the ECDH key, decrypts the message, and immediately returns both the plaintext bytes and an `EncryptionKeyProof` containing that ECDH key. [2](#0-1) 

The caller then checks `C::F::from_repr` before calling `batch.verify_with_vartime_blame`; on a non-canonical scalar it returns `InvalidShare` with `Some(blame)`, bypassing verification of the queued PoP. [5](#0-4) 

The processor forwards that blame material in `ProcessorMessage::InvalidShare`, exposing the serialized `EncryptionKeyProof`. [6](#0-5) 

### Impact Explanation
The disclosed `EncryptionKeyProof.key` is the ECDH point `recipient_private_key * attacker_supplied_message_key`. If the attacker copied that message key from an encrypted share sent by another participant, the disclosed point is also the decryption key for the original ciphertext. [7](#0-6) 

The attacker can therefore decrypt the copied share and recover the victim sender’s `SecretShare`. This is direct secret-share leakage from a remotely supplied `EncryptedMessage`; for threshold one it can disclose the full DKG secret contribution, and for larger thresholds it exposes secret material that was intended to remain encrypted. [8](#0-7) 

### Likelihood Explanation
The attack needs one malformed share delivered as the sender’s authenticated share and knowledge of another encrypted share using the same message key. The attacker does not need to know the reused key’s discrete logarithm or produce a valid PoP because the premature canonical-scalar error bypasses the batch PoP check. [5](#0-4) 

The plaintext must merely fail `from_repr` after ChaCha20 decryption. For Ristretto’s 252-bit scalar field encoded in 32 bytes, uniformly distributed decrypted bytes are non-canonical with probability roughly `1 - 2^-4`, so a fixed ciphertext such as all-zero bytes triggers the vulnerable branch with high probability. [9](#0-8) [10](#0-9) 

### Recommendation
Do not attach or return `EncryptionKeyProof` until every queued PoP has passed `batch.verify_with_vartime_blame`. Store per-sender “decrypted value is non-canonical” state, finish decrypting and queueing all shares, verify the batch, and only return `InvalidShare { blame: Some(...) }` after the sender’s PoP is proven valid; otherwise return `blame: None`. [1](#0-0) 

A regression test should reuse a valid message key, corrupt the copied PoP, choose ciphertext likely to decrypt non-canonically, call `calculate_share`, and assert the error carries `blame: None` rather than an `EncryptionKeyProof`. [11](#0-10) 

### Proof of Concept
Conceptual attack against a Ristretto PedPoP recipient:

```text
Alice -> Bob valid EncryptedMessage:
  A = [key = xG][PoP for x][C_Alice]

Eve -> Bob forged EncryptedMessage:
  E = [key = xG][invalid PoP][C_Eve = 0x00 * 32]
```

Bob parses `E` through `EncryptedMessage::read`, queues the invalid PoP, decrypts `C_Eve` with `b * xG`, and creates a blame proof containing `b * xG`. [12](#0-11) [2](#0-1) 

Because `C_Eve` decrypts to pseudorandom bytes, `from_repr` is likely to reject it as non-canonical; the early error path returns `InvalidShare { participant: Eve, blame: Some(proof) }` before the invalid PoP is checked. [13](#0-12) 

Once `ProcessorMessage::InvalidShare` serializes that proof, Eve extracts the shared point, derives the ChaCha20 key through the same context transcript and static IV, and decrypts Alice’s original `C_Alice` into Alice’s secret share. [6](#0-5) [14](#0-13)

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L263-272)
```rust
impl<F: PrimeField> ReadWrite for SecretShare<F> {
  fn read<R: Read>(reader: &mut R, _: ThresholdParams) -> io::Result<Self> {
    let mut repr = F::Repr::default();
    reader.read_exact(repr.as_mut())?;
    Ok(SecretShare(repr))
  }

  fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.0.as_ref())
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L357-377)
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

    // Calculate our own share
    let share = polynomial(&self.coefficients, self.params.i());
    self.coefficients.zeroize();

    Ok((
      KeyMachine { params: self.params, secret: share, commitments, encryption: self.encryption },
```

**File:** crypto/dkg/pedpop/src/lib.rs (L474-499)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L78-91)
```rust
/// An encrypted message, with a per-message encryption key enabling revealing specific messages
/// without side effects.
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-132)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-499)
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
      },
```

**File:** processor/src/key_gen.rs (L416-425)
```rust
            (match machine.calculate_share(rng, shares) {
              Ok(res) => res,
              Err(e) => match e {
                PedPoPError::InvalidShare { participant, blame } => {
                  Err(ProcessorMessage::InvalidShare {
                    id,
                    accuser: params.i(),
                    faulty: participant,
                    blame: Some(blame.map(|blame| blame.serialize())).flatten(),
                  })?
```

**File:** crypto/dkg/pedpop/src/tests.rs (L175-205)
```rust
#[test]
fn invalid_encryption_pop_blame() {
  let (mut machines, commitment_msgs, _, mut secret_shares) =
    commit_enc_keys_and_shares::<_, Ristretto>(&mut OsRng);

  // Mutate the PoP of the encrypted message from 1 to 2
  secret_shares.get_mut(&ONE).unwrap().get_mut(&TWO).unwrap().invalidate_pop();

  let mut blame = None;
  let machines = machines
    .drain()
    .filter_map(|(i, machine)| {
      let our_secret_shares = generate_secret_shares(&secret_shares, i);
      let machine = machine.calculate_share(&mut OsRng, our_secret_shares);
      if i == TWO {
        assert_eq!(
          machine.err(),
          Some(PedPoPError::InvalidShare { participant: ONE, blame: None })
        );
        // Explicitly declare we have a blame object, which happens to be None since invalid PoP
        // is self-explainable
        blame = Some(None);
        None
      } else {
        Some(machine.unwrap())
      }
    })
    .collect::<Vec<_>>();

  test_blame(&commitment_msgs, machines, &secret_shares[&ONE][&TWO].clone(), &blame.unwrap());
}
```
