### Title
Secret share decrypted and ECDH blame proof generated before the per-message proof-of-possession is verified - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary

`Encryption::decrypt` in PedPoP verifies the sender's Schnorr proof-of-possession (`msg.pop`) only by *queueing* it into a deferred `BatchVerifier`, then immediately performs ECDH with the participant's long-term `enc_key` and applies the decryption keystream. In `KeyMachine::calculate_share`, when the decrypted plaintext fails to deserialize as a canonical scalar, the function returns early with a blame proof — `EncryptionKeyProof`, containing the ECDH shared point `key` and a DLEq proof over `enc_key` — *without ever calling `batch.verify`*. An unauthenticated, malicious participant therefore gets a verifiably-correct static-ECDH result `enc_key * P` for an arbitrary point `P` of their choice, before (and without) any PoP check on `P`.

### Finding Description

In `Encryption::decrypt`, the PoP signature is only queued for later batch verification, yet the sensitive operations — `ecdh::<C>(&self.enc_key, msg.key)`, keystream application, and construction of `EncryptionKeyProof { key, dleq: DLEqProof::prove(..., &self.enc_key) }` — execute immediately on the unauthenticated message. [1](#0-0) 

In `KeyMachine::calculate_share`, the decrypted share bytes are checked for canonicality and, on failure, the function returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }` via `?` — returning before `batch.verify_with_vartime_blame()` on line 493 ever validates the queued PoP. [2](#0-1) 

Note the contrast with `Decryption::decrypt_with_proof`, which verifies `msg.pop` synchronously *before* using any proof or key material — demonstrating the intended order is check-then-use. [3](#0-2) 

### Impact Explanation

This is a static-ECDH decryption oracle on the participant's long-term encryption key `enc_key`, reachable by any unprivileged DKG participant:

1. The attacker (as participant `l`) sends an `EncryptedMessage` via `EncryptedMessage::read` (key_gen.rs parses it) with `key = P` for an arbitrary point `P` — no valid PoP required — and garbage ciphertext.
2. `decrypt` computes `key = enc_key * P` and produces a DLEq proof binding `(G, P)` to `(enc_pub_key, key)`. The garbage plaintext fails `C::F::from_repr` with ~92% probability for Ristretto (random bytes ≥ group order), triggering the early `InvalidShare { blame: Some(blame) }` return before the batch PoP check runs.
3. `blame.key = enc_key * P`, publicly verifiable via the DLEq — the attacker obtains the correct DH output for a point whose discrete log they do not know, something they could never compute themselves.

With this oracle, the attacker recovers the shared key for *any* message encrypted to this participant: for an observed `EncryptedMessage` with ephemeral public key `E` (e.g., a blame-republished message), `P = E` yields `enc_key * E`, decrypting that participant's incoming secret-share ciphertexts — enabling recovery of other participants' secret shares directed to the victim, and potentially reconstructing the threshold secret. This maps directly to the report's class: the authorization check (PoP gating use of the decryption asset `enc_key`) is implemented after the protected resource is accessed.

### Likelihood Explanation

The attacker needs only to be a DKG participant sending a malformed `EncryptedMessage` — a fully unprivileged position within the threat model (public inputs to `EncryptedMessage::read`/`calculate_share`). The failure path triggers probabilistically per message (~92% for a random 32-byte ciphertext payload on Ristretto), and the attacker can retry across DKG sessions/attempts. The only constraint is one oracle query per `calculate_share` invocation since `self` is consumed, but each query is independent and publicly evidenced by the returned blame.

### Recommendation

Verify `msg.pop` synchronously inside `Encryption::decrypt` before performing `ecdh` or generating the `EncryptionKeyProof` — i.e., call `msg.pop.verify(msg.key, pop_challenge(...))` and return a blameless `InvalidShare` error (PoP failure is sender fault and needs no decryption proof, matching `DecryptionError::InvalidSignature` semantics in `blame_internal`). Alternatively, defer construction of `EncryptionKeyProof` until after `batch.verify_with_vartime_blame()` succeeds, and ensure no error path can emit `blame` for a message whose PoP was never verified.

### Proof of Concept

1. Honest participant `v` runs `SecretShareMachine`/`KeyMachine` with `enc_key`, `enc_pub_key = G*enc_key` (registered in `Decryption::enc_keys` via `register`).
2. Malicious participant `l` constructs `EncryptedMessage { key: P, pop: <invalid>, msg: <32 bytes of 0xFF> }` where `P` is the ephemeral `key` field of a previously observed message to `v` (dlog unknown to `l`).
3. `v`'s `calculate_share` calls `decrypt`: PoP is queued only; `key = enc_key * P` computed; keystream applied; `EncryptionKeyProof { key, dleq }` built.
4. `from_repr(0xFF.. ^ keystream)` almost surely fails → `Err(InvalidShare { participant: l, blame: Some(blame) })` returned before `batch.verify`.
5. `l` publishes/verifies `blame`: `dleq.verify(&encryption_key_transcript(ctx), &[G, P], &[enc_pub, blame.key])` succeeds, confirming `blame.key == enc_key * P` — the shared key for the observed ciphertext, which `l` now uses to decrypt it via `cipher(ctx, &blame.key)`.
6. Repeating across DKG attempts yields a persistent static-DH oracle on `enc_key`, enabling decryption of arbitrary messages addressed to `v` and recovery of secret shares sent to `v` — undermining the confidentiality of the DKG.

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-501)
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
