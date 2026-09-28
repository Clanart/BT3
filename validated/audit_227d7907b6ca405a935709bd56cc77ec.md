### Title
Blame proof revealing an ECDH shared key is produced before the forged PoP is verified, reintroducing the key-co-opting attack the PoP was designed to prevent - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
The PedPoP DKG defends against a key-co-opting attack (attacker reuses another participant's per-message encryption key `X` so the victim's blame proof reveals `bX`, exposing the honest participant's message) by requiring each `EncryptedMessage` to carry a Schnorr proof-of-possession over its ephemeral key. However, `Encryption::decrypt` only *queues* the PoP into a deferred `BatchVerifier`, and `KeyMachine::calculate_share` returns an `InvalidShare` error — carrying an `EncryptionKeyProof` that reveals the ECDH shared key — via an early `?` return when the decrypted share fails scalar deserialization, before `batch.verify_with_vartime_blame()` ever runs. An attacker can therefore bypass the PoP check entirely and trigger the exact side-effect the PoP exists to prevent.

### Finding Description
The incident class is a security check that is effectively bypassed because an effect executes before the check completes (TimelockController's reentrant `execute`). The Serai analog is an ordering defect between a deferred batch check and an early-return effect:

1. `Encryption::decrypt` calls `msg.pop.batch_verify(rng, &mut batch, batch_id, ...)`, which only *enqueues* the PoP statement — it does not verify it. It then computes `key = ecdh(&self.enc_key, msg.key)` (the shared key `b·msg.key`) and returns it inside an `EncryptionKeyProof` [1](#0-0) .
2. In `KeyMachine::calculate_share`, the returned `(share_bytes, blame)` is used; if `C::F::from_repr(share_bytes.0)` fails, the function returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` via `?` at line 480–482 — before the batch containing the PoP is verified at line 493 [2](#0-1) .
3. The `blame` value is an `EncryptionKeyProof` containing `key = b·msg.key`, i.e., the point form of the ECDH shared key. Once published as a blame proof, anyone can apply `cipher(context, key)` to decrypt the message originally encrypted under `msg.key` [3](#0-2) .
4. The code comments explicitly describe this attack: without the PoP, Eve reuses Alice's per-message key `X`, and Bob's blame argument "reveal[s] bX, revealing Alice's message to Bob" — called "a massive side effect which could break some protocols" [4](#0-3) . The intent was to distinguish PoP failures (`BatchId::Decryption → blame: None`) from share failures (`BatchId::Share → blame: Some`), but the deserialization early-return path attaches `blame: Some` without the PoP ever being evaluated [5](#0-4) .

### Impact Explanation
An unprivileged participant (or anyone able to submit a share message to a victim and observe the published blame) can:

1. Observe victim-destined `EncryptedMessage`s carrying public ephemeral keys `X_d` from each dealer `d`.
2. Send the victim a forged `EncryptedMessage` with `key = X_d`, any invalid PoP, and ciphertext bytes that deserialize to a non-canonical scalar (e.g., all `0xFF` — the same trick used by the crate's own `invalidate_share_serialization` test helper [6](#0-5) ).
3. The victim's `calculate_share` returns `blame: Some(EncryptionKeyProof { key: b·X_d, ... })`; per the protocol this blame is published (e.g., via `VerifyBlame`), revealing `b·X_d` and thus the plaintext of dealer `d`'s share to the victim.

Repeated for every dealer's share to one victim, the attacker recovers all n share contributions to that participant — i.e., the participant's full FROST secret share. This is exactly the key-share-recovery outcome the in-scope rules accept, and exactly the scenario the DKG spec says the PoP was added to eliminate.

### Likelihood Explanation
Requirements are modest: the attacker needs one DKG participant slot (or the ability to inject a share message attributed to a participant) and to see the victim's inbound encrypted share messages — which are broadcast/relayed in normal deployments since blame verification (`AdditionalBlameMachine::new`) is designed for third-party auditors. The forged message is trivially constructible: reuse a public `X`, supply any bytes for the PoP, and pad `msg` with a non-canonical encoding. No timing window, collusion, or cryptographic break is needed — it is deterministic, hence Medium-to-High likelihood; severity is bounded by the fact it recovers one participant's share per targeted victim rather than the group key outright.

### Recommendation
Verify the PoP *before* attaching the `EncryptionKeyProof` to any error path. Concretely, in `calculate_share`:

- Perform the PoP `batch_verify` queue and the `from_repr` check such that a deserialization failure only yields `blame: Some(..)` once the batch has been verified — e.g., defer the `from_repr` error, queue the share-verification statements conditionally, run `batch.verify_with_vartime_blame()` first, and map a `BatchId::Decryption(l)` failure to `blame: None` even when the scalar also fails to decode.
- Equivalently, treat "decrypted bytes aren't a canonical scalar" as a `BatchId::Decryption`-class fault (sender fault with no key-revealing proof) unless the PoP has already been verified — mirroring `blame_internal`, which correctly returns `sender` without evaluating the proof-revealing path when the signature is invalid [7](#0-6) .

### Proof of Concept
```
Setup: n=3 PedPoP DKG, attacker Eve = participant 3, victim Bob = participant 1.
Dealer Alice (participant 2) publishes EncryptedMessage { key: X, pop: σ, msg: enc_share(Alice→1) }.

1. Eve observes X from Alice's message.
2. Eve constructs EncryptedMessage':
     key  = X                         // co-opted per-message key
     pop  = SchnorrSignature { R: <arbitrary point>, s: <arbitrary scalar> }  // invalid PoP
     msg  = SecretShare([0xFF; 32])   // non-canonical scalar
   and sends it to Bob as her round-2 share.
3. Bob's KeyMachine::calculate_share:
     - decrypt() queues Eve's invalid PoP into `batch` (NOT verified),
       computes key' = ecdh(bob_enc_key, X) = b·X, and builds
       EncryptionKeyProof { key: b·X, dleq(valid) }.
     - C::F::from_repr([0xFF; 32]) returns None → early `?` returns
       PedPoPError::InvalidShare { participant: 3, blame: Some(proof) }
       BEFORE batch.verify_with_vartime_blame() executes.
4. Bob publishes the blame proof. Anyone computes
     cipher(context, b·X) and decrypts Alice's original msg → Alice's share to Bob.
5. Repeat with each dealer's message key X_d → Bob's full secret share is recovered.
```

Note: the analogous `BlameMachine::blame_internal` path is *not* vulnerable — it checks the PoP synchronously inside `decrypt_with_proof` and returns `sender` on `InvalidSignature` before any key material is revealed [8](#0-7) . Only the hot-path `decrypt`/`calculate_share` ordering leaks the proof before the check.

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-133)
```rust
fn cipher<C: Ciphersuite>(context: [u8; 32], ecdh: &Zeroizing<C::G>) -> ChaCha20 {
  // Ideally, we'd box this transcript with ZAlloc, yet that's only possible on nightly
  // TODO: https://github.com/serai-dex/serai/issues/151
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"encryption_key");

  let mut ecdh = ecdh.to_bytes();
  transcript.append_message(b"shared_key", ecdh.as_ref());
  ecdh.as_mut().zeroize();

  let zeroize = |buf: &mut [u8]| buf.zeroize();

  let mut key = Cc20Key::default();
  let mut challenge = transcript.challenge(b"key");
  key.copy_from_slice(&challenge[.. 32]);
  zeroize(challenge.as_mut());

  // Since the key is single-use, it doesn't matter what we use for the IV
  // The issue is key + IV reuse. If we never reuse the key, we can't have the opportunity to
  // reuse a nonce
  // Use a static IV in acknowledgement of this
  let mut iv = Cc20Iv::default();
  // The \0 is to satisfy the length requirement (12), not to be null terminated
  iv.copy_from_slice(b"DKG IV v0.2\0");

  // ChaCha20 has the same commentary as the transcript regarding ZAlloc
  // TODO: https://github.com/serai-dex/serai/issues/151
  let res = ChaCha20::new(&key, &iv);
  zeroize(key.as_mut());
  res
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L226-239)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L582-604)
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
```
