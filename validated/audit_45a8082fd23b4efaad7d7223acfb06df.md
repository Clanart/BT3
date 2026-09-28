### Title
Malformed PedPoP share leaks another participant’s ECDH-shared key - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Medium. `KeyMachine::calculate_share` creates a publishable `EncryptionKeyProof` before it enforces the proof-of-possession on the message’s ephemeral key. A participant can therefore reuse the ephemeral key from someone else’s encrypted share, submit malformed ciphertext, and induce the recipient to publish the ECDH secret for that reused key. Anyone who sees the blame can then decrypt the original victim ciphertext.

### Finding Description
`EncryptedMessage` contains a public ephemeral `key`, a Schnorr proof-of-possession `pop`, and ciphertext `msg`. The comments explicitly identify the intended attack: if an attacker reuses another message’s key and causes the recipient to reveal the corresponding ECDH value, the victim’s ciphertext is exposed; `pop` is supposed to prevent this. [1](#0-0) 

However, `Encryption::decrypt` does not verify `pop` before decrypting. It only queues the signature statement into a `BatchVerifier`, then immediately computes `ecdh = recipient_private * msg.key`, decrypts the ciphertext, and constructs an `EncryptionKeyProof` containing that ECDH point. [2](#0-1) 

`calculate_share` then immediately returns `InvalidShare` with `blame: Some(proof)` when the decrypted bytes are not a canonical scalar. The batch containing the queued `pop` verification is not checked until after this early return, so a deliberately invalid PoP still produces the ECDH-revealing blame proof. [3](#0-2) 

The resulting proof is sufficient to decrypt the original victim message: `decrypt_with_proof` verifies that the supplied key is the ECDH output for the claimed message key and recipient encryption key, then uses it directly as the ChaCha20 shared secret. [4](#0-3) 

### Impact Explanation
The attacker can disclose a Pedersen secret share that was encrypted exclusively for another participant. In PedPoP, each sender encrypts `polynomial(coefficients, recipient)` to that recipient, and the recipient adds the result to its private accumulated key share. [5](#0-4) [6](#0-5) 

This crosses the confidentiality boundary between the original sender and recipient: although the attacker cannot know the reused ephemeral key’s discrete logarithm, the malformed message still causes the recipient to publish the corresponding ECDH value as blame. That ECDH value is all that is needed to derive the ChaCha20 key and decrypt the victim’s original ciphertext.

### Likelihood Explanation
Exploitation requires the attacker to be able to submit one DKG share message and observe the target encrypted share’s public `msg.key`, both of which are explicit inputs to `EncryptedMessage::read`/`calculate_share`. [7](#0-6) 

The payload is trivial to construct: any canonical point can be copied as `key`, any canonical `R`/`s` pair can occupy `pop`, and a 32-byte all-`0xff` ciphertext decrypts to a non-canonical scalar for the expected scalar encodings. No valid PoP, discrete-log knowledge, or timing dependency is needed because the PoP is never reached before blame is emitted.

### Recommendation
Authenticate `msg.key` before deriving or exporting any decryption proof. The minimal fix is to verify `msg.pop` synchronously in `Encryption::decrypt` before calling `ecdh`; return an error without constructing `EncryptionKeyProof` when it fails.

Alternatively, split decryption into two phases: validate all PoPs first, then derive ECDH values and blame proofs only for authenticated messages. A regression test should submit an `EncryptedMessage` whose `key` is copied from another valid message but whose `pop` and ciphertext are invalid, and assert that `calculate_share` returns an error containing no ECDH `blame`.

### Proof of Concept
1. Alice honestly creates an encrypted share for Bob. Its wire encoding contains ephemeral key `X`, PoP `(R_A, s_A)`, and ciphertext `C_A`.
2. Eve parses or observes that wire message and extracts `X`.
3. Eve sends Bob an `EncryptedMessage` as Eve’s share with:
   - `key = X`
   - `pop` set to any canonical but invalid `SchnorrSignature`
   - `msg` set to bytes that decode as non-canonical plaintext after decryption, such as all `0xff`
4. Bob’s `calculate_share` calls `decrypt`. Eve’s PoP is queued into the batch, but `decrypt` still computes `K = bX`, where `b` is Bob’s private encryption key, and returns an `EncryptionKeyProof` containing `K`.
5. Because `K XOR C_Eve` is non-canonical, `calculate_share` returns early with `InvalidShare { blame: Some(K proof) }` before `batch.verify_with_vartime_blame()` rejects Eve’s PoP.
6. Bob publishes the blame. Any observer supplies Alice’s original `(X, R_A, s_A, C_A)` and Eve-induced proof to `decrypt_with_proof`; Alice’s PoP validates, the DLEq validates `K = bX`, and the function decrypts `C_A`, disclosing Alice’s secret share for Bob.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L81-91)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-176)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L366-370)
```rust
      let mut share = polynomial(&self.coefficients, l);
      let share_bytes = Zeroizing::new(SecretShare::<C::F>(share.to_repr()));
      share.zeroize();
      res.insert(l, self.encryption.encrypt(rng, l, share_bytes));
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L474-498)
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
```
