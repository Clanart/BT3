### Title
PedPoP leaks ECDH blame key for EncryptedMessages whose Schnorr PoP was never verified, enabling secret share recovery - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` decrypts each incoming `EncryptedMessage` and returns a blame proof — which contains the ECDH shared key — before the batch verifier ever checks the message's proof-of-possession. A malicious DKG participant can therefore reuse an honest sender's per-message encryption key, feed ciphertext that fails scalar deserialization, and receive a blame proof revealing the shared key that decrypts the honest sender's real secret share.

### Finding Description
`EncryptedMessage` carries a per-message public key `key` plus `pop`, a Schnorr PoP specifically added so "Eve [cannot] observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X" and trigger a blame statement that reveals `bX` — the shared key — thereby leaking Alice's message [1](#0-0) .

In `Encryption::decrypt`, the PoP is only *queued* onto a `BatchVerifier` (`msg.pop.batch_verify(...)`) while the ECDH shared point `key = ecdh(enc_key, msg.key)` and its DLEq blame proof are computed and returned immediately [2](#0-1) .

In `calculate_share`, the deserialized plaintext is checked with `C::F::from_repr`, and on failure the function returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` via `?` — an early return that never executes `batch.verify_with_vartime_blame()` [3](#0-2) . The blame proof — `EncryptionKeyProof { key, dleq }` proving `key = enc_key_bob * msg.key` — is handed to the caller even though the PoP for that `msg.key` was queued but never verified.

### Impact Explanation
The attacker (a DKG participant reachable purely through public protocol messages `EncryptedMessage::read` → `calculate_share`) copies the `key` field `X` from an honest sender's encrypted share to the victim Bob, attaches an invalid `pop`, and sets `msg` to arbitrary bytes. After XOR with the keystream, the plaintext is effectively random, so `C::F::from_repr` fails with high probability (the ed25519/Ristretto scalar modulus is ~2^252, so random 32-byte strings are non-canonical ~87% of the time; retryable). Bob's `calculate_share` errors out returning `blame = EncryptionKeyProof { key: bX, dleq }`, which callers are designed to publish. Whoever sees it recovers `bX` — the exact shared key protecting the honest sender's real secret share to Bob — and decrypts it via `cipher(context, bX)`, recovering an honest party's secret share. Repeating across victims/senders compromises the DKG's confidentiality and can enable threshold key recovery.

### Likelihood Explanation
Reachable by any DKG participant sending attacker-controlled bytes through `EncryptedMessage::read`/`calculate_share` — a public-input path. Success needs only a non-canonical decrypted scalar (~87% per attempt, repeatable), and no valid PoP, valid ciphertext, or honest cooperation. The only mitigation assumed elsewhere — that the PoP prevents exactly this — is voided by the ordering bug.

### Recommendation
In `calculate_share`, do not attach `blame` to the `from_repr` failure until the queued `BatchId::Decryption(l)` PoP statement has been verified. Either verify the PoP for that message before returning (run/flush the batch verifier, or verify `msg.pop` inline before producing `EncryptionKeyProof`), or withhold the blame proof when decryption-failure occurs prior to batch verification and instead report `InvalidShare { blame: None }`. More robustly, `Encryption::decrypt` should verify `pop` before computing/returning the ECDH blame material.

### Proof of Concept
```rust
// Eve is DKG participant j; Alice (i=a) sent Bob (i=b) an honest
// EncryptedMessage { key: X, pop, msg: ct_alice } for her secret share.
// Eve observes Alice's message on the (authenticated but readable) channel.

// 1) Eve crafts her own EncryptedMessage to Bob reusing Alice's per-message key:
let forged = EncryptedMessage::<C, SecretShare<C::F>> {
  key: alice_msg.key,          // X — Eve does NOT know its discrete log
  pop: SchnorrSignature { R: random_point, s: random_scalar }, // invalid PoP
  msg: Zeroizing::new(SecretShare(random_32_bytes)),           // garbage
};

// 2) Bob runs calculate_share:
//    decrypt() -> queues pop.batch_verify(batch, BatchId::Decryption(eve), ...)
//               -> key = ECDH(bob_enc_key, X)  (== Alice's shared key bX)
//               -> applies ChaCha20 keystream -> random plaintext
//               -> returns (plaintext, blame = EncryptionKeyProof { key: bX, dleq })
//    from_repr(plaintext) fails (w.h.p.) -> early return
//    Err(InvalidShare { participant: eve, blame: Some(blame) })
//    *** batch.verify_with_vartime_blame() is never reached; PoP never checked ***

// 3) Bob publishes the blame proof per protocol. Eve (or anyone) reads bX and runs:
//    cipher::<C>(context, &bX).apply_keystream(alice_msg.msg) -> Alice's share to Bob.
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
