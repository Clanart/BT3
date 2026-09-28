### Title
Blame proof revealing the ECDH shared key is emitted before the message's proof-of-possession is verified - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
In `KeyMachine::calculate_share`, each incoming `EncryptedMessage` is passed to `Encryption::decrypt`, which only *queues* the per-message Schnorr proof-of-possession (PoP) into a `BatchVerifier` and immediately returns the decrypted bytes together with an `EncryptionKeyProof` that reveals the ECDH shared key (`enc_key * msg.key`). If the decrypted bytes fail `C::F::from_repr`, the function returns `PedPoPError::InvalidShare { blame: Some(blame) }` early — before `batch.verify_with_vartime_blame()` ever runs — so the ECDH-key-revealing blame proof is emitted for a message whose PoP was never verified. This is exactly the side-channel the PoP exists to prevent (see the comment on `EncryptedMessage`), and mirrors the bug class of the reference report: an error path acting on/returning a resource whose authentication was never completed.

### Finding Description
`Encryption::decrypt` queues the PoP check (`msg.pop.batch_verify(...)`) into the caller-supplied `BatchVerifier` under `BatchId::Decryption(l)`, then unconditionally computes `key = ecdh(&self.enc_key, msg.key)` and returns an `EncryptionKeyProof` containing that shared key and a DLEq proving its correctness [1](#0-0) .

In `calculate_share`, the return value is used immediately: `C::F::from_repr(share_bytes.0)` failing produces `Err(InvalidShare { participant: l, blame: Some(blame.clone()) })` via `?`, which propagates out before the batch verifier (holding the only PoP verification) is executed [2](#0-1) . The PoP's sole purpose is to stop an attacker from causing a decryptor to reveal `bX` for an unauthenticated `msg.key` — the code comment explicitly states that without it, an attacker replaying Alice's key `X` would make Bob "reveal bX, revealing Alice's message to Bob" [3](#0-2) . The downstream error is broadcast with the blame attached (`ProcessorMessage::InvalidShare { blame: Some(blame) }`) [4](#0-3) .

### Impact Explanation
A malicious DKG participant Eve sends Bob an `EncryptedMessage` whose `key` field is copied from Alice's encrypted share to Bob (call it `X`), with a garbage PoP and randomized ciphertext. `decrypt` computes `bX` (Bob's ECDH shared key for `X`), the garbage decrypts to bytes that fail `from_repr` with high probability (a random 32-byte string is almost never a canonical scalar for ~2^252-modulus fields), and Bob returns/broadcasts `blame = EncryptionKeyProof { key: bX, dleq }`. Anyone who observed Alice's ciphertext to Bob can now apply the same ChaCha20 keystream derived from `bX` (`cipher(context, &proof.key)`) to recover Alice's secret share for Bob, leaking key-share material and defeating the confidentiality the PoP was designed to guarantee. Although `Decryption::decrypt_with_proof` would later reject the accusation due to the invalid PoP [5](#0-4) , the key has already been revealed at accusation time.

### Likelihood Explanation
Requires only that the attacker is (or impersonates, where the authenticated channel permits) a DKG participant able to send a share message to a victim and observe the victim's incoming `msg.key` values — the same threat model the code comments already assume. The trigger is a single malformed message and succeeds whenever the garbage plaintext is non-canonical.

### Recommendation
Do not emit the `EncryptionKeyProof` until the PoP for that message has actually verified. Concretely: hold `(l, blame)` pairs and the batch such that any early error path either (a) runs `batch.verify_vartime_with_vartime_blame()` first and returns `blame: None` when `BatchId::Decryption(l)` fails, or (b) returns the share-deserialization failure only after the batch check passes. Alternatively, have `Encryption::decrypt` verify the PoP synchronously (or return the proof wrapped so it is only released on batch success), so no code path can surface an ECDH-key reveal for an unauthenticated message.

### Proof of Concept
1. Alice → Bob: legitimate `EncryptedMessage` with per-message key `X` (observable field).
2. Eve → Bob: `EncryptedMessage { key: X, pop: <any well-formed SchnorrSignature>, msg: <random bytes> }`.
3. Bob's `calculate_share` calls `decrypt`, which queues the (invalid) PoP into the batch and returns `EncryptionKeyProof { key: ecdh(bob_enc_key, X), dleq }`.
4. `from_repr` on the garbage plaintext fails → `InvalidShare { participant: Eve, blame: Some(proof) }` is returned and broadcast before `batch.verify_with_vartime_blame()` runs.
5. Recipient of the blame message extracts `proof.key = bX`, reconstructs `cipher::<C>(context, &bX)`, and decrypts Alice's previously observed ciphertext to Bob, recovering Alice's share for Bob.

Unverified caveat: this assumes the victim's error path broadcasts the attached blame proof (as `processor/src/key_gen.rs` does with `ProcessorMessage::InvalidShare`) and that the attacker can observe the victim's incoming `msg.key`, consistent with the threat model documented in `EncryptedMessage`.

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L373-397)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L469-501)
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
  }
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

**File:** processor/src/key_gen.rs (L416-426)
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
```
