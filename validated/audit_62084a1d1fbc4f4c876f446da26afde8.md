### Title
PedPoP emits ECDH-revealing blame proof for messages whose PoP was never verified - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The referenced CVE concerns acting on untrusted input (dev-container configuration) before a trust decision is made. The Serai analog exists in PedPoP's `KeyMachine::calculate_share`: an attacker's encrypted share is decrypted (ECDH + ChaCha20) and an `EncryptionKeyProof` — which reveals the ECDH shared key — is generated and returned as blame **before** the message's proof-of-possession (PoP) signature is ever verified. The PoP exists specifically to prevent this leak, but the verification is deferred into a `BatchVerifier` that is never executed on the early-error path.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, `EncryptedMessage` carries a Schnorr PoP over the ephemeral encryption `key`, whose sole purpose is documented at lines 84-91: without it, "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X … they'd reveal bX, revealing Alice's message to Bob." [1](#0-0) 

`Encryption::decrypt` only *queues* the PoP into a `BatchVerifier` (`msg.pop.batch_verify(...)`, encryption.rs:479-485), then immediately computes the ECDH key and decrypts, always producing an `EncryptionKeyProof` containing `key = ecdh(self.enc_key, msg.key)` (encryption.rs:487-499). [2](#0-1) 

In `calculate_share` (lib.rs:476-499), the decrypted bytes are parsed with `C::F::from_repr`; on failure the function returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` via `?` — **before** `batch.verify_with_vartime_blame()` at line 493 is reached. The queued PoP check for that message is never executed. [3](#0-2) 

### Impact Explanation
An unprivileged DKG participant Eve reads Alice's encrypted share to Bob from the authenticated broadcast channel, extracts Alice's per-message encryption key `X`, and sends Bob her own `EncryptedMessage` with `key = X`, a garbage ciphertext, and a forged/invalid PoP. Bob's `decrypt` computes `bX` (Bob's encryption private key times Alice's ephemeral key — the shared key protecting Alice's real message), decrypts to garbage, and with ~50% probability (non-canonical scalar encoding) hits the `from_repr` early-return, emitting a blame proof that reveals `bX` plus a valid DLEq for it — without ever checking Eve's forged PoP. Anyone observing the blame proof can decrypt Alice's original secret share to Bob. Across two honest victims this leaks real DKG secret shares; repetition across attempts makes recovery of shares effectively deterministic, enabling key-share recovery of the threshold key. This is precisely the "massive side effect" the code comments say the PoP prevents — defeated purely by verification ordering.

### Likelihood Explanation
The attacker only needs to be a DKG participant (or anyone able to deliver a `EncryptedMessage` claiming to be from a participant to a victim processor, as in `processor/src/key_gen.rs` `EncryptedMessage::read` → `calculate_share`). No key compromise needed: copying `key` bytes and writing garbage ciphertext plus any 64-byte "signature" suffices. Each attempt succeeds whenever the garbage fails `from_repr` (~1/2 per try), and the same ECDH key covers the victim's real message, so one success fully compromises that share. Batch-verification ordering — not cryptography — is the only broken assumption.

### Recommendation
Verify the PoP synchronously (or run `batch.verify_with_vartime_blame` for the decryption-batch IDs) **before** using decrypted bytes or releasing any `EncryptionKeyProof`. Specifically, in `calculate_share`, when `from_repr` fails, do not attach the blame proof unless the queued PoP verification for that participant has already succeeded; alternatively have `Encryption::decrypt` return the proof only after a confirmed PoP, or attach `blame: None` whenever the message's PoP cannot be proven valid — a malformed share under an unproven PoP should blame the sender without disclosing the ECDH key.

### Proof of Concept
1. Run a PedPoP session with participants Alice(1), Bob(2), Eve(3) on `Ristretto`.
2. Observe Alice→Bob `EncryptedMessage` on the broadcast channel; extract `msg.key = X`.
3. Eve constructs `EncryptedMessage { key: X, pop: <arbitrary bytes parsed as SchnorrSignature>, msg: <32 bytes of garbage> }` addressed to Bob (sender index = Eve, or spoofed as any index whose blame we want to trigger).
4. Bob's `KeyMachine::calculate_share` calls `encryption.decrypt`, which queues the forged PoP, computes `bX = enc_key_b * X`, decrypts to garbage.
5. `C::F::from_repr(garbage)` fails → `PedPoPError::InvalidShare { participant: 3, blame: Some(EncryptionKeyProof { key: bX, dleq: valid }) }` is returned; the batch verifier holding Eve's invalid PoP is dropped unverified.
6. Decrypt Alice's ciphertext to Bob with `cipher(context, bX)` to recover Alice's secret share for Bob — key-share material leaked solely via untrusted bytes processed before its trust check.

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
