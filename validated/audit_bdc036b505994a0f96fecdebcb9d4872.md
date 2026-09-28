### Title
Blame proof (ECDH key) is emitted before the encrypted message's proof-of-possession is verified, letting a malicious participant trigger public disclosure of an honest participant's secret shares - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to the PraisonAI report (unauthenticated endpoint disclosing sensitive data), `KeyMachine::calculate_share` returns an `EncryptionKeyProof` — which contains the ECDH shared decryption key for a message — on the share-deserialization failure path *before* the message's Schnorr proof-of-possession (PoP) has been verified. The PoP exists specifically to prevent a malicious party from copying an honest participant's per-message encryption key and thereby tricking a victim into publishing a blame proof that decrypts the honest party's message. Because the PoP is only checked inside the deferred `BatchVerifier`, an early `Err` return leaks the decryption key for an unauthenticated message, publicly revealing an honest sender's secret share.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, `Encryption::decrypt` unconditionally computes the ECDH shared key and an `EncryptionKeyProof` (a DLEq proving `key = enc_priv * msg.key`) for every message, only *queuing* the PoP into the batch verifier: [1](#0-0) 

The comments in the same file document the exact attack the PoP is meant to prevent: Eve copies Alice's per-message key `X` into her own message to Bob; Bob's blame reveals `bX`, decrypting Alice's real message to Bob: [2](#0-1) 

In `KeyMachine::calculate_share`, the decrypted bytes are converted with `C::F::from_repr`, and on failure the function returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` immediately — before `batch.verify_with_vartime_blame()` is ever reached, so the forged PoP is never checked: [3](#0-2) 

The processor then serializes and broadcasts this blame proof in `ProcessorMessage::InvalidShare` (`processor/src/key_gen.rs:419-425`), making the ECDH shared key public. Anyone can apply `cipher::<C>(context, &proof.key)` to Alice's original ciphertext (as `decrypt_with_proof` does at `encryption.rs:392`) and recover the plaintext `SecretShare`.

### Impact Explanation
An unprivileged participant (Eve) causes each honest victim Bob to publicly disclose the ECDH key that decrypts Alice's secret share to Bob. Repeating once per victim recipient reveals `f_alice(j)` for every `j`, allowing full reconstruction of Alice's contributed secret polynomial — i.e., key share recovery of an honest, non-faulty participant. Even a single leaked share degrades the threshold security margin. The disclosure is permanent and public (blame is broadcast/on-chain via `VerifyBlame`), matching the "unauthenticated information disclosure of confidential material" bug class: the sensitive data (the decryption key) is released for a message that was never authenticated as legitimately using that key.

### Likelihood Explanation
The attack requires only standard protocol participation: Eve must be a DKG participant able to send one malformed `EncryptedMessage` to a victim during `generate_secret_shares`, and observe the honest victim's published blame. Alice's `msg.key` is public in her broadcast ciphertext, and no PoP private-key knowledge is needed since the forged PoP is never verified on this path. No collusion, no broken BFT assumptions — a single malicious participant suffices, and one malicious participant is within the threat model PedPoP blame is designed to handle.

### Recommendation
Do not return the `EncryptionKeyProof` until the PoP has been verified. In `KeyMachine::calculate_share`, the `from_repr` failure branch should either (a) defer blame-proof release until after `batch.verify_with_vartime_blame()` confirms the `BatchId::Decryption(l)` statement passed, or (b) synchronously verify `msg.pop` (via `SchnorrSignature::verify`, as `decrypt_with_proof` does) before attaching `blame` to the error. More robustly, `Encryption::decrypt` could return a handle that only yields the proof once its PoP is confirmed, making the ordering enforceable by construction rather than by call-site discipline.

### Proof of Concept
1. Participants run PedPoP DKG. Alice (participant 1) sends an honest `EncryptedMessage` to Bob (participant 2) with per-message key `X = x·G`; ciphertext `C = share ⊕ keystream(ecdh(bob_enc, X))`.
2. Eve (participant 3) constructs `EncryptedMessage { key: X, pop: <arbitrary invalid signature>, msg: <0xFF… bytes that fail `C::F::from_repr`> }` and sends it to Bob as her share.
3. Bob's `calculate_share` calls `Encryption::decrypt`, which computes `proof.key = ecdh(bob_priv, X)` (identical to Alice's shared key) and a valid DLEq. `from_repr` fails, so `PedPoPError::InvalidShare { participant: Eve, blame: Some(proof) }` is returned before the batch PoP check.
4. The processor publishes `proof` in `ProcessorMessage::InvalidShare`. Any observer computes `cipher(context, proof.key)` and applies it to Alice's public ciphertext `C`, recovering Alice's secret share to Bob.
5. Repeating against each victim recipient yields all of Alice's shares, recovering her contributed secret — while blame adjudication will name Eve faulty, the disclosure is already irreversible.

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
