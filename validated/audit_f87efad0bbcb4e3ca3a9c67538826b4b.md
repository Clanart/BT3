### Title
Blame proof reveals ECDH shared key before the message's proof-of-possession is verified, leaking honest participants' secret shares - (crypto/dkg/pedpop/src/lib.rs)

### Summary

The JetLeak bug class — an error-handling path that leaks sensitive key material to a remote, unauthenticated party in response to attacker-controlled input — has a direct analog in PedPoP's blame flow. `KeyMachine::calculate_share` decrypts every incoming `EncryptedMessage` and unconditionally builds an `EncryptionKeyProof` containing the ECDH shared key (`proof.key`), intended to let third parties decrypt the offending message. However, when the decrypted share bytes fail `C::F::from_repr`, the function returns `PedPoPError::InvalidShare { blame: Some(blame) }` at `crypto/dkg/pedpop/src/lib.rs:480-482` *before* `batch.verify_with_vartime_blame()` is ever invoked at `crypto/dkg/pedpop/src/lib.rs:493` — so the per-message Schnorr proof-of-possession queued via `BatchId::Decryption(l)` at `crypto/dkg/pedpop/src/lib.rs:479-485` is never evaluated on this path. The blame proof revealing the ECDH key is emitted even for a message whose PoP is invalid.

### Finding Description

`Encryption::decrypt` at `crypto/dkg/pedpop/src/encryption.rs:469-501` always computes `key = ecdh(&self.enc_key, msg.key)` and returns an `EncryptionKeyProof { key, dleq }` regardless of whether `msg.pop` is valid; the PoP is merely queued into the caller's batch verifier. In `calculate_share`, the loop over `shares` decrypts each message, and a non-canonical decrypted scalar triggers an early `?` return carrying `blame.clone()` — bypassing the single `batch.verify_with_vartime_blame()` call that runs only after the loop completes.

The code comments at `crypto/dkg/pedpop/src/encryption.rs:84-90` document exactly this attack: "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X… they'd reveal bX, revealing Alice's message to Bob." The PoP is the stated mitigation. It fails here because the blame proof is published on a code path where the PoP was never checked. `cipher()` at `crypto/dkg/pedpop/src/encryption.rs:101-133` derives the ChaCha20 keystream solely from `context` and the ECDH point, so revealing `ecdh(enc_key_Bob, X)` exposes the keystream for *every* message addressed to Bob that uses ephemeral key `X` — including Alice's original `SecretShare` ciphertext, which an observer can then decrypt directly.

Downstream, the processor publishes this blame (`ProcessorMessage::InvalidShare { blame }`, `processor/src/key_gen.rs:504+` `VerifyBlame`), so the leaked ECDH point is broadcast to all participants before `decrypt_with_proof`'s signature check (`encryption.rs:374-379`) blames the attacker — at which point the share is already exposed.

### Impact Explanation

An unprivileged DKG participant (or anyone able to feed an `EncryptedMessage` to a victim's `calculate_share`, e.g. via coordinator-routed share messages) learns an honest participant's polynomial evaluation at the victim's index — i.e. the secret share Alice contributed to Bob. Since PedPoP sums all shares into the victim's FROST secret (`self.secret += share`), recovering shares progressively compromises threshold key material: with knowledge of `t` shares' contributions an attacker can bias/recover group key material, and each leaked share directly leaks that sender's VSS evaluation. This is secret-key material leaked to remote parties via a crafted public input — precisely the class of bug (sensitive data exposure through an error path) as JetLeak.

### Likelihood Explanation

The attack requires: (1) observing an honest `EncryptedMessage` to the victim (authenticated channel messages are typically relayed/broadcast), (2) sending the victim a crafted `EncryptedMessage` reusing the observed `msg.key` point with arbitrary ciphertext and a syntactically valid (but false) `SchnorrSignature` encoding — `SchnorrSignature::read` only needs canonical R and s. The decrypted bytes must be a non-canonical scalar, which for Ristretto/ed25519-order fields occurs with probability ~15/16 per attempt (l ≈ 2²⁵² over 32 bytes), and retries are free across attempts/participants. Once the early return fires, the victim emits `Some(blame)` containing the ECDH point. No valid PoP, valid share, or collusion is needed. Likelihood: high, constrained only by needing to be a DKG participant (or reach the message path).

### Recommendation

Do not emit the `EncryptionKeyProof` until the message's PoP has been verified. Restructure `calculate_share` so that blame is only attached after `batch.verify_with_vartime_blame()` resolves the `BatchId::Decryption(l)` queue for that participant — e.g., collect decrypted bytes and defer `from_repr` validation until after batch verification, returning `blame: None` (sender at fault) when the PoP failed and only releasing the ECDH-key proof for messages with a verified PoP. Alternatively, verify `msg.pop` eagerly inside `Encryption::decrypt` before computing/returning the ECDH proof.

### Proof of Concept

```
// Victim B runs KeyMachine::calculate_share over shares from participants.
// Attacker E (participant l) observed honest A's EncryptedMessage to B:
//   (key = X = kG, pop_A, ciphertext_A)  -- Alice's SecretShare to B.
//
// E constructs:
//   msg_E.key = X                                // copied ephemeral key
//   msg_E.pop = SchnorrSignature { R: <any valid point>, s: <any scalar> }  // invalid PoP
//   msg_E.msg = <32 arbitrary bytes>             // decrypts under cipher(context, enc_key_B * X)
//
// Inside calculate_share:
//   encryption.decrypt(...) queues E's pop into `batch` (never verified),
//   computes key = ecdh(enc_key_B, X), decrypts msg_E -> garbage bytes.
//   C::F::from_repr(garbage) == None  (probability ~1 - l/2^256 ≈ 15/16)
//   => early return PedPoPError::InvalidShare { participant: E, blame: Some(blame) }
//      where blame.key = enc_key_B * X is broadcast in the blame proof.
//
// Any observer computes keystream = cipher(context, blame.key) and XORs
// ciphertext_A to recover Alice's secret share for B. batch.verify_with_vartime_blame()
// (which would have flagged E's bad PoP as BatchId::Decryption(E) -> blame: None)
// is never reached because of the early `?` at lib.rs:481.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L84-91)
```rust
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
  pop: SchnorrSignature<C>,
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-393)
```rust
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
