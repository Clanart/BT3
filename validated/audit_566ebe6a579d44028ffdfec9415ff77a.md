### Title
Invalid encrypted DKG shares disclose the recipient’s ECDH key before PoP verification - ([File: crypto/dkg/pedpop/src/lib.rs])

### Summary
`EncryptedMessage::read` accepts attacker-controlled ephemeral keys, PoPs, and ciphertexts from serialized input. [1](#0-0)  During `KeyMachine::calculate_share`, `Encryption::decrypt` queues the sender’s proof-of-possession for later batch verification, but immediately derives the recipient’s ECDH key and constructs a revealing `EncryptionKeyProof`. [2](#0-1)  If the decrypted bytes are not a canonical scalar, `calculate_share` returns `PedPoPError::InvalidShare` with that proof attached before the queued PoP batch is verified. [3](#0-2) 

### Finding Description
`Encryption::decrypt` authenticates the ciphertext only indirectly by queueing `msg.pop.batch_verify`; it does not wait for that proof to be verified before deriving `key = enc_key * msg.key` and returning it inside an `EncryptionKeyProof`. [4](#0-3)  The caller treats scalar-deserialization failure as an immediately reportable share fault and stores the returned blame proof before executing `batch.verify_with_vartime_blame`. [5](#0-4)  Consequently, an attacker can copy the ephemeral public key from an honest encrypted share into a new attacker-submitted `EncryptedMessage`, use an invalid PoP, and choose ciphertext bytes whose decrypted value is likely non-canonical. [1](#0-0)  The resulting error reveals the ECDH point for the copied ephemeral key because the blame proof embeds `key` and a DLEq proof tying it to the victim message key. [6](#0-5) 

### Impact Explanation
The disclosed ECDH point is the exact decryption key consumed by the stream cipher and is sufficient for `decrypt_with_proof` to decrypt the copied victim ciphertext after the proof is supplied. [7](#0-6)  This exposes the honest sender’s secret share and can contribute to recovery of threshold key material. [8](#0-7)  The code’s own PoP comment identifies precisely this side effect: an invalid accusation that causes the recipient to reveal `bX` also reveals Alice’s message to Bob. [9](#0-8) 

### Likelihood Explanation
An attacker who can provide a serialized share to `EncryptedMessage::read` and induce a recipient to run `calculate_share` can mount the attack without knowing the copied ephemeral private key or producing a valid PoP. [1](#0-0)  The attacker needs the malformed ciphertext to decrypt to a non-canonical scalar, which can be retried with attacker-selected ciphertexts; once parsing fails, `calculate_share` returns the disclosure before batch verification rejects the invalid PoP. [10](#0-9)  The issue is therefore an authentication-ordering/oracle flaw analogous to a weak or missing authentication tag, not reliance on brute-forcing a valid signature. [11](#0-10) 

### Recommendation
Do not derive, return, or publish an `EncryptionKeyProof` until the message’s PoP has been verified successfully. [12](#0-11)  In `calculate_share`, verify the PoP batch before scalar decoding and before converting a decryption result into `Some(blame)`, or split message-authentication failures from share faults so invalid-PoP messages return no decryption proof. [13](#0-12)  Prefer authenticated encryption, or at minimum treat the PoP as the authentication tag and make all plaintext parsing and key disclosure conditional on its successful verification. [14](#0-13) 

### Proof of Concept
```text
1. Observe an honest EncryptedMessage M from participant A to recipient B.
   Let M.key = kG and M.msg = C.

2. Construct M':
     M'.key = M.key
     M'.pop = any syntactically valid SchnorrSignature encoding
     M'.msg = attacker-controlled ciphertext C'

3. Submit M' as the attacker's encrypted share for B.

4. EncryptedMessage::read accepts all three attacker-controlled fields.
   Encryption::decrypt queues M'.pop for batch verification, but then:
     shared = B_enc_private * M'.key
     plaintext = ChaCha20(shared, C')
     proof = EncryptionKeyProof { key: shared, dleq: ... }

5. If plaintext is not a canonical scalar, calculate_share returns:
     InvalidShare { participant: attacker, blame: Some(proof) }

6. This happens before batch.verify_with_vartime_blame rejects M'.pop.
   The attacker obtains shared = B_enc_private * M.key.

7. Use the disclosed shared key to decrypt the copied honest ciphertext C,
   recovering A's secret share for B.
```

The decisive ordering is visible in `calculate_share`: decryption and proof construction occur first, scalar decoding can return `Some(blame)`, and only afterward is the batch containing the PoP verified. [13](#0-12)

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-393)
```rust
    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L469-499)
```rust
  pub(crate) fn decrypt<R: RngCore + CryptoRng, I: Copy + Zeroize, E: Encryptable>(
    &self,
    rng: &mut R,
    batch: &mut BatchVerifier<I, C::G>,
    // Uses a distinct batch ID so if this batch verifier is reused, we know its the PoP aspect
    // which failed, and therefore to use None for the blame
    batch_id: I,
    from: Participant,
    mut msg: EncryptedMessage<C, E>,
  ) -> (Zeroizing<E>, EncryptionKeyProof<C>) {
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
