### Title
Blame proof released before encryption-key PoP is verified — reuse of another sender's ephemeral key causes public disclosure of DKG secret shares - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` in `crypto/dkg/pedpop` returns an `EncryptionKeyProof` — which publicly reveals the ECDH shared key for a message — before the Schnorr proof-of-possession on that message's ephemeral key has actually been verified. The PoP is only *queued* into a `BatchVerifier`, and the early error return for a non-canonical share scalar happens before `batch.verify_with_vartime_blame()` is ever reached. A malicious participant Eve can copy the ephemeral key point `X` from honest Alice's encrypted share to victim Bob into Eve's own message to Bob (with garbage ciphertext and a garbage PoP). Bob decrypts with `bX` (Alice's real ECDH key), the plaintext almost surely fails `C::F::from_repr`, and Bob emits a blame proof revealing `bX`. Once published, `bX` decrypts Alice's real share to Bob — exactly the attack the in-code comment says the PoP was added to prevent. [1](#0-0) [2](#0-1) 

### Finding Description
`Encryption::decrypt` unconditionally computes `key = ecdh(&self.enc_key, msg.key)` and returns an `EncryptionKeyProof { key, dleq }`, while the PoP (`msg.pop`) is only registered via `msg.pop.batch_verify(...)` into a caller-supplied `BatchVerifier`. [3](#0-2) 

In `calculate_share`, the per-sender loop decrypts, then immediately attempts `C::F::from_repr(share_bytes.0)`; on failure it returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` via `?` — exiting the function *before* `batch.verify_with_vartime_blame()` on line 493 ever runs. Therefore the PoP that was supposed to prove the sender knows the discrete log of `msg.key` is never checked for this message, yet the blame proof disclosing `bX` has already been produced and returned to the caller (the processor serializes it into `ProcessorMessage::InvalidShare`/`Blame` and broadcasts it). [2](#0-1) [4](#0-3) 

The code comment in `EncryptedMessage` explicitly describes this threat: without the PoP, "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X… they'd reveal bX, revealing Alice's message to Bob." The PoP exists, but the enforcement ordering leaves it unverified on this error path. [5](#0-4) 

### Impact Explanation
By copying each honest sender's per-recipient ephemeral key into poisoned messages and triggering the early `from_repr` failure, Eve causes every victim `j` to publish `enc_key_j * X_{i→j}`. This yields the plaintext `share_i(j)` — an evaluation of sender `i`'s secret DKG polynomial — for every `(i, j)` pair Eve poisons. Collecting shares at `t` (or all `n`) indexes lets Eve interpolate each sender's polynomial and recover its constant term; summing those recovers the full threshold group private key — complete secret-key compromise via publicly broadcast blame data. This mirrors CVE-2020-6438's class: a policy check (the PoP) exists but is not enforced on the path that discloses sensitive material.

### Likelihood Explanation
Requires only that Eve be a DKG participant who can observe other participants' share messages (the protocol's own threat model assumes this) and send her own authenticated share messages. Each poisoned message fails `from_repr` with overwhelming probability (~15/16 for a 252-bit field in a 32-byte representation), so a single attempt per victim suffices. No collusion or broken BFT is needed.

### Recommendation
Verify the PoP *synchronously* (or reorder so `batch.verify_with_vartime_blame()` covering all `BatchId::Decryption` entries completes) before any `EncryptionKeyProof` is returned or attached to an error. Concretely: in `calculate_share`, do not return early on `from_repr` failure; instead record the failure, let the batch verify first, and only release the blame for message `l` if `l`'s `BatchId::Decryption` statement passed. Alternatively have `Encryption::decrypt` verify `msg.pop` inline before emitting the proof.

### Proof of Concept
In `crypto/dkg/pedpop/src/tests.rs` style (Ristretto, `THRESHOLD`/`PARTICIPANTS`):

1. Run `commit_enc_keys_and_shares` so participant 3 (Alice) produces `EncryptedMessage` `m3→1` to participant 1 (Bob); record `m3→1.key = X`.
2. Construct Eve's (participant 2) message `m2→1`: set `m2→1.key = X`, `m2→1.pop = SchnorrSignature { R: random_point, s: random_scalar }` (invalid), `m2→1.msg = random bytes`.
3. Bob calls `machine.calculate_share(rng, shares)` including `m2→1`.
4. Observe it returns `Err(PedPoPError::InvalidShare { participant: 2, blame: Some(proof) })` — even though `m2→1.pop` is invalid — because the `from_repr` early return precedes `batch.verify_with_vartime_blame()`.
5. Assert `*proof.key == m3→1.key * enc_key_1_priv` and that `cipher(CONTEXT, &proof.key)` applied to `m3→1.msg` yields Bob's valid share from Alice, i.e. `share_3(1)` is recovered from the published blame alone.

Uncertain elements: whether `C::read_G` rejects non-canonical/identity encodings does not affect this finding (Eve copies a valid point verbatim); the exact probability of `from_repr` failure depends on the field modulus but is high for all in-scope curves.

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

**File:** processor/src/key_gen.rs (L416-425)
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
```
