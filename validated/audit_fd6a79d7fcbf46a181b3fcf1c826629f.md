### Title
ECDH decryption key revealed in blame proof before the message's proof-of-possession is verified - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The reported bug class is an unchecked result from a security-relevant call. In `KeyMachine::calculate_share`, the Schnorr proof-of-possession (PoP) on each `EncryptedMessage`'s ephemeral key is only queued into a `BatchVerifier`, while the decrypted share bytes are consumed — and an ECDH-revealing blame proof is returned — before that batch (which contains the PoP statement) is ever verified. A malicious sender can therefore force the recipient to produce an `EncryptionKeyProof` revealing the ECDH shared point for an ephemeral key the sender does not know the discrete log of, bypassing the exact protection the PoP was added for.

### Finding Description
`Encryption::decrypt` queues `msg.pop.batch_verify(...)` into the caller-supplied `batch` and then unconditionally decrypts and returns `(msg, EncryptionKeyProof { key: ecdh(enc_key, msg.key), dleq })` [1](#0-0) .

In `calculate_share`, the per-sender loop calls `decrypt`, then immediately checks `C::F::from_repr(share_bytes.0)` and, on failure, returns `Err(PedPoPError::InvalidShare { participant: l, blame: Some(blame) })` — an early `?` that exits before `batch.verify_with_vartime_blame()` at line 493 ever runs [2](#0-1) . The queued PoP statements are discarded unverified.

The comment on `EncryptedMessage` explains why the PoP exists: without it, an attacker who reuses someone else's observed ephemeral key could induce a blame proof revealing `bX` and decrypt that message [3](#0-2) . The `blame`/`decrypt_with_proof` path enforces this by checking `msg.pop.verify` before using the revealed key [4](#0-3) , but `calculate_share` defeats the ordering: the blame proof is emitted on a deserialization error prior to PoP verification.

### Impact Explanation
A malicious DKG participant (sender) observes an honest party's `EncryptedMessage` to victim Bob with ephemeral key `K`. The attacker sends Bob a secret-share `EncryptedMessage` with `key = K` and ciphertext that decrypts to a non-canonical scalar. Bob's `calculate_share` returns `InvalidShare { participant: attacker, blame: Some(proof) }` where `proof.key = enc_key_bob * K`. Blame proofs are designed to be published for adjudication; once published, the attacker learns the ECDH shared point for `K` and can decrypt the honest party's secret share to Bob. The attacker cannot produce a valid PoP for `K` (unknown discrete log), so this leak is only possible because the PoP result is never checked on this path. This recovers a DKG secret share that the recipient otherwise treats as protected.

### Likelihood Explanation
Reachable by any unprivileged DKG participant with public inputs: they supply an `EncryptedMessage` to `calculate_share` containing an arbitrary `key` point (read via `C::read_G` in `EncryptedMessage::read`) and ciphertext they generate via `encrypt`. Triggering requires only a non-canonical plaintext share, trivially constructed. Exploitation additionally requires the blame proof to be published or otherwise observed, which is the intended purpose of the returned `blame` field, so Medium rather than High.

### Recommendation
Check the PoP result before emitting or relying on the decryption. Reorder `calculate_share` so the batched verification (or at least the `BatchId::Decryption` PoP statements) completes before the `from_repr` deserialization error path can return a blame proof containing `proof.key`; alternatively, have `Encryption::decrypt` verify the PoP eagerly and return `Err` instead of a blame proof when it fails, so no ECDH-derived material is exposed for an unauthenticated ephemeral key.

### Proof of Concept
1. Bob runs `KeyGenMachine::generate_coefficients` then `generate_secret_shares`, registering attacker Mallory's `EncryptionKeyMessage`; Alice's encrypted share to Bob uses ephemeral key `K` (observed by Mallory via the authenticated broadcast channel).
2. Mallory calls `encrypt(rng, context, mallory_i, enc_key_bob, garbage_bytes)` where `garbage_bytes` are bytes for which `C::F::from_repr` returns `None` (e.g., all-0xFF on ed25519/ristretto fields per the test helper `invalidate_share_serialization` [5](#0-4) ), then overwrites the resulting `EncryptedMessage.key` with Alice's `K` and re-signs nothing (she cannot produce a PoP for `K`).
3. Mallory submits this message in `shares` to Bob's `calculate_share`.
4. `decrypt` queues the (invalid) PoP and returns `EncryptionKeyProof { key: enc_key_bob * K, dleq }`; `from_repr` fails on the garbage and line 480-482 returns `InvalidShare { blame: Some(proof) }` before line 493 verifies the batch.
5. Bob's caller publishes the blame proof for adjudication; Mallory now holds `enc_key_bob * K`, derives the ChaCha20 key via `cipher(context, proof.key)`, and decrypts Alice's secret share to Bob — despite never having possessed `K`'s discrete log.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L84-90)
```rust
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L218-239)
```rust
  #[cfg(test)]
  pub(crate) fn invalidate_share_serialization<R: RngCore + CryptoRng>(
    &mut self,
    rng: &mut R,
    context: [u8; 32],
    from: Participant,
    to: C::G,
  ) {
    use ciphersuite::group::ff::PrimeField;

    let mut repr = <C::F as PrimeField>::Repr::default();
    for b in repr.as_mut() {
      *b = 255;
    }
    // Tries to guarantee the above assumption.
    assert_eq!(repr.as_ref().len(), self.msg.as_ref().len());
    // Checks that this isn't over a field where this is somehow valid
    assert!(!bool::from(C::F::from_repr(repr).is_some()));

    self.msg.as_mut().as_mut().copy_from_slice(repr.as_ref());
    *self = encrypt(rng, context, from, to, self.msg.clone());
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-379)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
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
