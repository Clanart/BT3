### Title
Blame proof reveals ECDH key for messages whose proof-of-possession was never verified, enabling secret share disclosure - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP encryption layer includes a Schnorr proof-of-possession (PoP) on each `EncryptedMessage` specifically to prevent an attacker from copying another sender's per-message encryption key `X`, causing the victim to publish the ECDH value `enc_key * X` inside a blame proof and thereby revealing the honest sender's share. However, `KeyMachine::calculate_share` only *queues* the PoP into a `BatchVerifier` and returns the `EncryptionKeyProof` (containing the raw ECDH key) on an early `?` exit before `batch.verify_with_vartime_blame()` is ever executed. An unprivileged participant can therefore submit an `EncryptedMessage` carrying a copied public key and an *invalid* PoP and still force the victim to reveal the ECDH shared key, decrypting the honest party's secret share.

### Finding Description
`Encryption::decrypt` in `crypto/dkg/pedpop/src/encryption.rs` computes `ecdh(&self.enc_key, msg.key)` unconditionally and returns an `EncryptionKeyProof` holding the raw shared point and a DLEq proof, while the PoP is merely queued via `msg.pop.batch_verify` [1](#0-0) .

In `KeyMachine::calculate_share` (`crypto/dkg/pedpop/src/lib.rs`), after each `decrypt` call the decrypted bytes are checked with `C::F::from_repr`; on failure the function returns `PedPoPError::InvalidShare { blame: Some(blame) }` via `?` at lines 480–482, *before* `batch.verify_with_vartime_blame()` at line 493 ever validates the queued PoP signatures [2](#0-1) .

`EncryptedMessage::read` performs no PoP verification either — `key` is an arbitrary attacker-supplied point from `C::read_G` [3](#0-2) . The code comment itself documents this exact attack as the reason the PoP exists [4](#0-3) .

### Impact Explanation
This is the Serai analog of shared-namespace information disclosure: a party that only supplies public protocol inputs (a signed/relayed `EncryptedMessage`) forces a victim to emit a blame artifact that decrypts *another* participant's confidential secret share. In the Serai processor flow, the resulting `VerifyBlame`/`Blame` message publishes the `EncryptionKeyProof` to the coordinator and all validators (`processor/src/key_gen.rs` `CoordinatorMessage::VerifyBlame` handler reads the proof and calls `AdditionalBlameMachine::blame`), so the ECDH key `b·X` becomes public. With `X` copied from the honest sender's share message to the same victim, `b·X` is exactly that message's cipher key (`cipher(context, ecdh)`), so Alice's secret share to Bob is decrypted network-wide. With `t` shares disclosed this way (attacker repeats per victim), the threshold group key is recoverable; even one disclosed share is a confidentiality break the protocol explicitly claims to prevent.

### Likelihood Explanation
Requirements are minimal: the attacker is a DKG participant (or anyone able to inject a share message attributed to themselves to an honest participant's `calculate_share`) and observes the victim-bound `EncryptedMessage` of an honest sender to copy its `key` field — public data in the relayed message. No valid PoP, no ECDH private key, and no collusion is needed, because the PoP check is batched and never runs on the early-error path. The only requirement is that the forged ciphertext decrypts under the copied ECDH key to non-canonical scalar bytes, which is trivially satisfied by random ciphertext. Likelihood is moderate-to-high in any deployment where an attacker can send a share message, though it requires the victim to run `calculate_share` and publish the resulting blame — which is the normal protocol flow.

### Recommendation
Do not emit an `EncryptionKeyProof` until the message's PoP has been verified. Concretely, in `KeyMachine::calculate_share` (crypto/dkg/pedpop/src/lib.rs), either:

- verify the PoP synchronously (or run a partial `batch.verify`) before returning `InvalidShare` with `Some(blame)`, returning `blame: None` (self-evident sender fault) when the PoP fails; or
- restructure `Encryption::decrypt` so the ECDH `key`/proof is only produced after PoP verification succeeds.

Additionally, `Decryption::decrypt_with_proof`/`blame_internal` should treat a `proof` attached to a message whose PoP is invalid as sender fault without decrypting (it already checks the PoP first, which is correct — keep that ordering as the model for the `calculate_share` path).

### Proof of Concept
Setup: PedPoP DKG with `n ≥ 2`, attacker Eve (participant E), victim Bob (B), honest Alice (A).

1. Alice runs `generate_secret_shares`, producing `EncMsg(A→B)` with `key = Y = y·G`, `pop` valid, and ciphertext `ct = share_AB ⊕ keystream(cipher(ctx, b·Y))` where `b` is Bob's static `enc_key`. Eve observes `Y` (public field of the relayed message).
2. Eve constructs `EncMsg(E→B)`: `key = Y` (copied), `pop` = arbitrary bytes parsed by `SchnorrSignature::read` (invalid), `msg` = random bytes `r` of `SecretShare` length.
3. Bob runs `calculate_share` with the shares map including Eve's forged message. In `Encryption::decrypt`:
   - `pop.batch_verify` queues a statement that will fail — but nothing verifies yet;
   - `key = ecdh(b, Y) = b·Y` — identical to Alice's shared key with Bob;
   - `cipher(ctx, b·Y)` XORs `r` into `share_bytes`; with overwhelming probability `C::F::from_repr` yields `None`.
4. `calculate_share` hits `ok_or_else(|| PedPoPError::InvalidShare { participant: E, blame: Some(blame) })?` at lib.rs:480–482 and returns **before** `batch.verify_with_vartime_blame()` at line 493.
5. Bob publishes `blame`, an `EncryptionKeyProof { key: b·Y, dleq }`. `AdditionalBlameMachine::blame` / `VerifyBlame` verifies the DLEq against Bob's registered `enc_pub_key` and `Y`, then computes `cipher(ctx, b·Y)` — Eve's message decrypts to garbage so Eve is blamed, but now *anyone* can apply `cipher(ctx, b·Y)` to Alice's `ct` and recover `share_AB`, Alice's secret share to Bob.
6. Repeating against `t` distinct victims' shares (or waiting for abort/retry rounds) discloses enough shares to reconstruct the threshold private key.

The critical ordering defect: the PoP that exists solely to bind `key` to its owner is queued for batch verification, yet the blame artifact that leaks `b·key` is released on an earlier `?` path that bypasses the batch check entirely.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L83-90)
```rust
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-176)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
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
