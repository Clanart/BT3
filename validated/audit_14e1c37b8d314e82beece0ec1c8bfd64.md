### Title
Early error return in `KeyMachine::calculate_share` publishes the ECDH blame key before the message's proof-of-possession is verified, leaking honest shares - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`calculate_share` decrypts every incoming `EncryptedMessage` and immediately produces an `EncryptionKeyProof` (the ECDH shared key plus a DLEq proof) meant for blame attribution. The per-message proof-of-possession (PoP) signature is only *queued* into a `BatchVerifier` during `decrypt`, yet the function returns early with `blame: Some(blame)` when the decrypted bytes fail `C::F::from_repr` — before `batch.verify_with_vartime_blame()` ever runs. An unprivileged participant can copy an honest sender's per-message ephemeral key `X` into a forged message, forcing the victim to publish `b·X`, which decrypts the honest party's real secret share to the victim. This is exactly the key-co-opting attack the PoP was added to prevent, bypassed by exception ordering.

### Finding Description
In `crypto/dkg/pedpop/src/lib.rs`, `Encryption::decrypt` only enqueues the PoP check into the batch verifier via `msg.pop.batch_verify(...)`, then returns the decrypted bytes and an `EncryptionKeyProof` unconditionally [1](#0-0) . Back in `calculate_share`, the loop calls `decrypt`, then hits `from_repr`; a non-canonical scalar triggers `Err(PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) })?` — an early return that skips `batch.verify_with_vartime_blame()` entirely [2](#0-1) . The code comments in `encryption.rs` explicitly describe this attack: without the PoP, "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X… they'd reveal bX, revealing Alice's message to Bob" [3](#0-2) . The published blame propagates through `ProcessorMessage::InvalidShare` → `CoordinatorMessage::VerifyBlame`, where `AdditionalBlameMachine::blame` runs `decrypt_with_proof` — and since `proof` is `Some`, the PoP *is* checked there, but the ECDH key `b·X` has already been broadcast [4](#0-3) [5](#0-4) .

### Impact Explanation
Revealing `b·X` lets every observer run `cipher(context, bX)` and recover the plaintext of the honest participant's secret share to the victim (the message Eve copied `X` from). That share is a summand of the victim's `ThresholdKeys` secret; combined with the victim's share of Eve's own polynomial, an attacker reduces the secret entropy and, repeated across attempts/participants, can enable key-share recovery of the threshold key. Severity: High — secret material exposed by a single malformed public message.

### Likelihood Explanation
Any DKG participant (or anyone who can inject an authenticated message as a participant) can do this: copy `msg.key` from a victim's inbound `EncryptedMessage`, attach arbitrary ciphertext and any PoP bytes, and send it. The victim's `decrypt` computes `ecdh(enc_key, X)`, the garbage plaintext fails `from_repr` with high probability (non-canonical 32-byte encoding), and the early `?` returns the blame before the batched PoP check executes. Even when `from_repr` succeeds, ordering still never verifies the PoP on that error path — though the batch failure may then resolve to `BatchId::Decryption` and return `blame: None`.

### Recommendation
Do not attach `blame` until the PoP has actually been verified. Restructure `calculate_share` so that decryption/scalar-parse failures are recorded per participant, the `batch.verify_with_vartime_blame()` runs first, and `EncryptionKeyProof`s are only released for participants whose PoP verified but whose share failed deserialization or verification. Alternatively, have `decrypt` verify the PoP synchronously before producing the `EncryptionKeyProof`.

### Proof of Concept
```rust
// PedPoP with honest sender Alice (A) and victim Bob (B); Eve (E) is a participant.
// 1. Alice -> Bob: EncryptedMessage { key: X, pop: sig_X, msg: enc(share_AB) }.
// 2. Eve reads Alice's message, extracts X, and sends Bob:
//    EncryptedMessage { key: X, pop: <arbitrary bytes>, msg: <random ciphertext> }.
// 3. In Bob's calculate_share:
//    - decrypt() queues pop.batch_verify (never verified on this path),
//    - ecdh(enc_key_b, X) = bX decrypts garbage,
//    - C::F::from_repr(garbage) fails ->
//      return Err(InvalidShare { participant: E, blame: Some(EncryptionKeyProof{ key: bX, dleq }) })
//      before batch.verify_with_vartime_blame() runs.
// 4. Bob's processor emits ProcessorMessage::InvalidShare{ blame: bX } which is broadcast.
// 5. Anyone computes cipher(context, bX) and decrypts Alice's real message,
//    recovering Alice's secret share to Bob.
```

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L83-91)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-500)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L582-593)
```rust
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
```

**File:** processor/src/key_gen.rs (L416-427)
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
                }
                _ => panic!("unknown error: {e:?}"),
```
