### Title
Blame proof for a non-canonical secret share is emitted before the encryption-key proof-of-possession is verified, letting any party reuse a victim's ephemeral key to extract the ECDH shared key and decrypt the victim's share - (File: crypto/dkg/pedpop/src/lib.rs, crypto/dkg/pedpop/src/encryption.rs)

### Summary
The axios advisory leaks a credential (XSRF token) by attaching it to requests sent to arbitrary hosts. The Serai analog: `KeyMachine::calculate_share` publishes a blame proof (`EncryptionKeyProof`) containing the recipient's ECDH shared key for a message whose proof-of-possession (`pop`) has not yet been verified. The very attack the `pop` field was added to prevent — an attacker reusing Alice's per-message encryption key `X` so that Bob's blame disclosure `b·X` decrypts Alice's share — still works whenever the decrypted plaintext is not a canonical scalar, because that error path returns the blame proof without ever running the batch verification that checks `pop`.

### Finding Description
`Encryption::decrypt` queues the `pop` Schnorr signature into a `BatchVerifier` (deferred verification), then unconditionally computes `key = ecdh(self.enc_key, msg.key)`, decrypts the ciphertext, and returns the plaintext together with a publishable `EncryptionKeyProof { key, dleq }` [1](#0-0) .

In `KeyMachine::calculate_share`, the loop over incoming shares calls `decrypt`, and if `C::F::from_repr(share_bytes.0)` fails it immediately returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` via the `?` operator [2](#0-1) . Only after the loop completes does `batch.verify_with_vartime_blame()` run, which is what actually verifies the `pop` signatures (mapped to `BatchId::Decryption(l)` → `blame: None`) [3](#0-2) .

So a sender who attaches a *stolen* key commitment `msg.key = X` (taken from a previous legitimate `EncryptedMessage` from Alice to Bob) with a garbage `pop` and any ciphertext that decrypts to non-canonical bytes forces the honest recipient to emit a blame proof revealing `b·X` — where `b` is the recipient's long-term encryption key and `X` is Alice's ephemeral key. The code comments describe precisely this attack and claim the `pop` prevents it: "When they do, they'd reveal bX, revealing Alice's message to Bob. This is a massive side effect" [4](#0-3) . The `pop` is never checked on this path, so the mitigation is bypassed.

The recipient's blame output is meant for publication (the processor propagates it in `ProcessorMessage::InvalidShare { blame }`), so `b·X` plus its valid DLEq proof reaches all participants [5](#0-4) . Anyone can then decrypt Alice's original message `msg_Alice` via `cipher(context, b·X)` and recover the secret share Alice sent to Bob [6](#0-5) .

### Impact Explanation
Disclosure of `b·X` lets the attacker decrypt the victim's ECDH-protected `SecretShare`, i.e. Alice's polynomial evaluation `f_Alice(Bob)`. Each leaked share is a point on a degree `t-1` polynomial contributing to the group secret; a passive observer collecting `t-1` leaked/owned shares plus this stolen one can reconstruct that participant's secret coefficient and reduce the effective security of the DKG output. This matches the report's class exactly: a secret credential (the ECDH share key) is attached to an outgoing message (the blame proof) destined for parties it was never intended for, because a check that was supposed to gate the disclosure was skipped on this path.

### Likelihood Explanation
Fully reachable by an unprivileged DKG participant (or anyone who can submit a `shares` message attributed to a participant over the authenticated channel):

1. Observe any legitimate `EncryptedMessage` Alice→Bob and copy its `key` field `X`.
2. Send to Bob an `EncryptedMessage { key: X, pop: <garbage>, msg: <garbage> }`. The PoK cannot be made valid (no knowledge of `x`), but `pop` is only verified inside the batch at the end of the loop.
3. The garbage ciphertext decrypts under `cipher(context, b·X)` to pseudorandom bytes; for `dalek-ff-group`/`Ristretto`'s ~252-bit field, a random 32-byte repr is non-canonical with overwhelming probability (≈ 15/16 per attempt), and the attacker can retry across messages/attempts.
4. `from_repr` fails → `calculate_share` returns `InvalidShare` with `blame: Some(proof)` before the batch ever runs → Bob publishes `EncryptionKeyProof` revealing `b·X` → attacker decrypts Alice's share.

No collusion, no malicious node, and no protocol-documented "MUST" violation are required — `blame` is returned even though `decrypt` itself could detect nothing wrong.

### Recommendation
Do not release the `EncryptionKeyProof` before the `pop` batch verification has succeeded. Concretely:

- In `calculate_share`, accumulate the per-sender blame proofs but only attach `blame: Some(...)` for errors discovered *after* `batch.verify_with_vartime_blame()` completes; for the `from_repr` failure path, defer the error (record `(l, blame)` as pending) and run the batch verification first — the `BatchId::Decryption(l)` arm already correctly maps to `blame: None`, so the fix is to ensure that arm is actually reached.
- Alternatively, verify `msg.pop` synchronously (or in an inner batch) inside `decrypt` before computing/returning the ECDH `key`, so a forged-key message can never cause a proof to be materialized.

### Proof of Concept
```rust
// Attacker (or malicious relayer) workflow, given a prior legitimate
// EncryptedMessage<Ristretto, SecretShare> `alice_msg` from Alice to Bob:

// 1. Reuse Alice's ephemeral key; supply any invalid PoP and ciphertext.
let mut forged = EncryptedMessage::<Ristretto, SecretShare<_>> {
  key: alice_msg.key,                 // X = k*G, attacker's choice
  pop: SchnorrSignature { R: random_point, s: random_scalar }, // INVALID
  msg: SecretShare(random_32_bytes),  // decrypts to garbage under b*X
};

// 2. Bob calls KeyMachine::calculate_share(shares containing `forged`).
//    Inside encryption.decrypt: pop is queued in `batch`, NOT verified;
//    key = ecdh(bob_enc_key, X) = b*X is computed and wrapped in an
//    EncryptionKeyProof, then returned.

// 3. cipher(context, b*X) XORs garbage -> from_repr fails (w.h.p.)
//    -> `?` returns PedPoPError::InvalidShare { participant: attacker,
//       blame: Some(proof_containing_b_X) } WITHOUT running
//    batch.verify_with_vartime_blame() — the Decryption(l) arm that
//    would have produced blame: None is never reached.

// 4. Bob publishes the blame proof. Attacker (and everyone else) now has
//    b*X and a valid DLEq proof; they run
//      cipher::<Ristretto>(context, &bX).apply_keystream(alice_msg.msg)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-393)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L476-482)
```rust
    for (l, share_bytes) in shares.drain() {
      let (mut share_bytes, blame) =
        self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
```

**File:** crypto/dkg/pedpop/src/lib.rs (L487-499)
```rust
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

**File:** processor/src/key_gen.rs (L419-425)
```rust
                PedPoPError::InvalidShare { participant, blame } => {
                  Err(ProcessorMessage::InvalidShare {
                    id,
                    accuser: params.i(),
                    faulty: participant,
                    blame: Some(blame.map(|blame| blame.serialize())).flatten(),
                  })?
```
