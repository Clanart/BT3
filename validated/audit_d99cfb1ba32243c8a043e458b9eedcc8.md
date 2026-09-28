### Title
PedPoP encrypted share messages are not bound to their intended recipient, enabling framing of honest participants via cross-recipient replay — (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The proof-of-possession (`pop`) authenticating each `EncryptedMessage` binds the sender (`from`), the per-message key, and the ciphertext — but never the recipient. The ECDH decryption key, however, is derived from the recipient's static encryption key. An attacker who obtains a copy of `sender -> Bob`'s encrypted secret share (these messages are broadcast/collected for blame anyway, and an unauthenticated network attacker can inject messages) can deliver it to Carol as "from sender". Carol's `calculate_share` decrypts it under Carol's key, producing garbage, then publishes an `EncryptionKeyProof` that is *valid* for Carol's registered encryption key. `BlameMachine::blame`/`AdditionalBlameMachine::blame` then verifies the proof, decrypts the garbage share, and blames the honest sender — a wrongful-fault outcome against a participant who followed the protocol correctly.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, `encrypt` computes the PoP as `pop_challenge::<C>(context, pub_nonce, pub_key, from, msg)` where `from` is the sender's own index (`self.i` in `Encryption::encrypt`); the recipient's identity or encryption key is never transcribed [1](#0-0) . Decryption verifies the same challenge and derives the ChaCha20 key via `ecdh(self.enc_key, msg.key)`, which is recipient-specific [2](#0-1) . Blame evaluation in `decrypt_with_proof` verifies the accuser's DLEq against `self.enc_keys[&decryptor]` — i.e., the accusing recipient's own encryption key — so a proof for a misdelivered message verifies cleanly [3](#0-2) . Then `blame_internal` checks the decrypted plaintext: since it was encrypted for a different recipient's key, it is either a non-canonical scalar or fails `share_verification_statements`, and in both cases returns `sender` as the faulty party [4](#0-3) . The processor consumes this via `CoordinatorMessage::VerifyBlame` -> `AdditionalBlameMachine::new(...).blame(accuser, accused, ...)` [5](#0-4) .

### Impact Explanation
This is the direct analog of an authorization/binding failure: the message authenticates *who sent it* but not *who may use it*. An honest dealer's correctly-formed share, replayed to a different participant, results in the honest dealer being adjudicated faulty — in Serai's deployment, "faulty" maps to a fatal slash of the validator, while the actual attacker (the relayer) is never identified. The PoP exists precisely to stop framing side-effects of the blame system (as documented in the spec and code comments), yet it fails to bind the one field needed to prevent cross-recipient replay.

### Likelihood Explanation
The attack requires only obtaining an `EncryptedMessage` ciphertext addressed to another participant and relaying it to a different recipient — no key compromise, no collusion, no broken BFT. In any DKG deployment where shares transit a gossip/multicast medium, or where blame transcripts are published, these ciphertexts are available to unprivileged parties. The PoP verifies regardless of the intended recipient, so delivery succeeds deterministically.

### Recommendation
Bind the recipient into the proof-of-possession and cipher context: extend `pop_challenge` to append the recipient's `Participant` index (or their registered `enc_key`), and include the same value in `cipher`'s transcript. Then verify in `decrypt`/`decrypt_with_proof` that the bound recipient equals the local `params.i()`/`decryptor`. This makes a replayed message fail the PoP check (`DecryptionError::InvalidSignature`), correctly attributing fault only when the sender produced a malformed message for *this* recipient.

### Proof of Concept
1. Honest Alice runs PedPoP with n ≥ 3; her `encrypt(rng, Bob, share)` produces `EncryptedMessage { key: A_b, pop, msg }` using Bob's `enc_key`. [6](#0-5) 
2. Attacker forwards the identical bytes to Carol. Carol calls `calculate_share`, mapping sender = Alice. `decrypt` verifies `pop` against `pop_challenge(context, pop.R, msg.key, Alice, ciphertext)` — it passes, since the challenge never names Bob or Carol [7](#0-6) .
3. Carol's ECDH `enc_key_Carol * A_b` yields the wrong stream; `C::F::from_repr` fails or the share fails `share_verification_statements`, producing `PedPoPError::InvalidShare { participant: Alice, blame: Some(proof) }` where `proof` DLEqs `enc_key_Carol * A_b` — a valid proof [8](#0-7) .
4. Any `AdditionalBlameMachine` (or the processor's `VerifyBlame`) runs `decrypt_with_proof` with `decryptor = Carol`: the DLEq verifies against `enc_keys[Carol]`, the garbage plaintext fails the share check, and `blame` returns `Alice` — the honest sender — as faulty [9](#0-8) .

Result: an unprivileged relayer deterministically causes an honest DKG participant to be blamed/slashed, because the message's authorization check (the PoP) omits the recipient it is intended for.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L135-167)
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
    msg,
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L366-397)
```rust
  pub(crate) fn decrypt_with_proof<E: Encryptable>(
    &self,
    from: Participant,
    decryptor: Participant,
    mut msg: EncryptedMessage<C, E>,
    // There's no encryption key proof if the accusation is of an invalid signature
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Result<Zeroizing<E>, DecryptionError> {
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }

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
    } else {
      Err(DecryptionError::InvalidProof)
    }
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L469-500)
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
    )
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

**File:** crypto/dkg/pedpop/src/lib.rs (L575-609)
```rust
  fn blame_internal(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };

    let Some(share) = Option::<C::F>::from(C::F::from_repr(share_bytes.0)) else {
      // If this isn't a valid scalar, the sender is faulty
      return sender;
    };

    // If this isn't a valid share, the sender is faulty
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
    }

    // The share was canonical and valid
    recipient
  }
```

**File:** processor/src/key_gen.rs (L543-549)
```rust
        let substrate_blame = AdditionalBlameMachine::new(
          context(&id, SUBSTRATE_KEY_CONTEXT),
          params.n(),
          substrate_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, substrate_share, substrate_blame);
```
