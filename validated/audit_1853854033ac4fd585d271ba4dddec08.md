### Title
Blame proof generated before PoP verification lets an attacker leak the ECDH key of an honest party's encrypted share - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`Encryption::decrypt` computes the ECDH shared key and wraps it in a publishable `EncryptionKeyProof` (`blame`) before the message's proof-of-possession is verified — the PoP is only queued into a `BatchVerifier`. In `KeyMachine::calculate_share`, a message that decrypts to a non-canonical scalar causes an early `?` return carrying `blame: Some(blame)`, so the batch PoP check is never reached. This re-opens exactly the attack the PoP was added to prevent (documented in `encryption.rs`): an attacker copies the per-message key `X` from an honest Alice→Bob `EncryptedMessage`, attaches it to a garbage ciphertext with an invalid PoP, and the honest recipient's `InvalidShare` error publishes `bX` — the ECDH key that also decrypts Alice's real message to Bob.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs:469-501`, `decrypt` queues `msg.pop` into `batch` (line 479-485) and *then* immediately computes `key = ecdh(&self.enc_key, msg.key)` and returns `EncryptionKeyProof { key, dleq }` (lines 487-499). The proof is created unconditionally, before the queued PoP is verified.

In `crypto/dkg/pedpop/src/lib.rs:474-499` (`KeyMachine::calculate_share`), the per-sender loop calls `decrypt`, and on `C::F::from_repr` failure returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }` via `ok_or_else`/`?` — short-circuiting before `batch.verify_with_vartime_blame()` at line 493 runs. The `BatchId::Decryption(l)` → `blame: None` mapping (lines 493-498) was designed to withhold the ECDH key when the PoP fails, but the early `from_repr` return bypasses it entirely. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
Like the advisory (backend secrets swept into a public artifact by overly broad collection), an ECDH key meant to stay private is swept into a publicly-published blame artifact because key generation is performed outside the scope the PoP was meant to gate. The published `key = b·X` is a valid `EncryptionKeyProof` (its DLEq verifies against `msg.key = X` and Bob's registered `enc_key`), so anyone can run `cipher(context, bX)` over Alice's original ciphertext — via `Decryption::decrypt_with_proof` (`encryption.rs:366-397`) — and recover the secret share Alice intended only for Bob. Leaked cross-participant shares of the Pedersen polynomials erode the DKG's secrecy and can feed share-recovery attacks.

### Likelihood Explanation
An unprivileged participant Eve needs only public inputs: observe Alice's `EncryptedMessage` to Bob on the authenticated channel, extract `key` (`C::G`), and send Bob a share message `{ key: X, pop: arbitrary, msg: garbage }`. Bob's `decrypt` returns a blame proof for `bX`; the garbage ciphertext decrypts under `bX` to bytes that are non-canonical scalars with high probability (a random 32-byte `Repr` fails `from_repr` for the ed25519/Ristretto scalar field ~75% of the time, and Eve can pick any bytes, so failure is what she wants anyway — she cannot know which bytes would parse, but any non-canonical result triggers the leak). Bob returns `InvalidShare { blame: Some(proof) }`, which per protocol is broadcast. Eve retries each DKG round.

### Recommendation
Do not build the `EncryptionKeyProof` (or at minimum do not release it) until the PoP for that message has been verified. Options: verify `msg.pop` synchronously inside `decrypt` instead of batching it when blame material is produced, or defer the `blame`/`from_repr` early-return until after `batch.verify_with_vartime_blame()` so `BatchId::Decryption` failures correctly yield `blame: None`. Reordering `calculate_share` to run the batch verification before emitting per-share errors achieves this.

### Proof of Concept
```rust
// Eve observes Alice->Bob EncryptedMessage; copies its `key` (X) field.
let mut forged: EncryptedMessage<C, SecretShare<C::F>> =
  EncryptedMessage { key: X_from_alice, pop: arbitrary_signature, msg: garbage_bytes };

// Bob runs calculate_share:
//  decrypt() queues the (invalid) PoP, computes key = ecdh(bob_enc_key, X) = bX,
//  and returns EncryptionKeyProof { key: bX, dleq }.
//  from_repr(decrypted garbage) == None  =>
//    return Err(InvalidShare { participant: Eve, blame: Some(proof_with_bX) })
//  -- batch.verify_with_vartime_blame() is never reached; the bad PoP is undetected.

// Bob publishes the blame proof. Anyone verifies dleq over [G, X] -> [bob_enc_key, bX]
// and calls decrypt_with_proof(Alice, Bob, alice_msg_to_bob, proof) to recover
// Alice's secret share to Bob — the exact disclosure the PoP was added to prevent.
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
