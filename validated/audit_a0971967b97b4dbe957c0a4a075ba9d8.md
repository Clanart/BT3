### Title
Blame proof (ECDH decryption key) emitted before proof-of-possession verification lets an attacker deanonymize co-opted messages and recover a victim's secret shares - ([File: crypto/dkg/pedpop/src/lib.rs])

### Summary
The node-fetch advisory describes credentials bound to one origin being forwarded to an untrusted origin. The Serai analog lives in PedPoP's blame protocol: `KeyMachine::calculate_share` attaches an `EncryptionKeyProof` — the recipient's raw ECDH shared key for a message — to an `InvalidShare` error *before* the batched Schnorr proof-of-possession on that message is ever verified. The PoP exists specifically to prevent this leak: comments in `encryption.rs` state that without it, an attacker who copies another participant's per-message key `X` can trigger a blame that reveals `bX`, decrypting the honest participant's message. Because `decrypt()` only *queues* the PoP into the `BatchVerifier` and `calculate_share` returns early on a non-canonical scalar (before `batch.verify_with_vartime_blame()`), the designed protection is bypassed.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs:469-501`, `Encryption::decrypt` schedules `msg.pop.batch_verify(...)` into a deferred batch and immediately returns `(msg, EncryptionKeyProof { key, dleq })` where `key = ecdh(self.enc_key, msg.key)` is the shared key able to decrypt any ciphertext produced under `msg.key` with this context (`cipher()` at `encryption.rs:101-133`).

In `crypto/dkg/pedpop/src/lib.rs:476-499`, `calculate_share` does:

```rust
let (mut share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
let share = Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
  PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
})?);
```

The `?` on a failed `from_repr` returns `InvalidShare` with the blame proof while the PoP is still only queued — `batch.verify_with_vartime_blame()` at line 493 is never reached. The code comments at `encryption.rs:84-90` acknowledge exactly this attack: a copied key plus induced blame would reveal Alice's message; the PoP is the mitigation. That mitigation is silently defeated by ordering.

An attacker Eve observes Alice's `EncryptedMessage<C, SecretShare>` to Bob (per-message key `pub_key`, Schnorr `pop`). Eve crafts `EncryptedMessage { key: alice.pub_key, pop: alice.pop, msg: garbage }` and sends it as her share to Bob. Bob's `decrypt` computes `key = enc_priv_B * alice.pub_key` and returns the proof; decryption of garbage yields bytes that are overwhelmingly non-canonical as a scalar (`C::F::from_repr` rejects any repr ≥ group order, ≈7/8 of random 32-byte strings for ed25519/Ristretto scalars — and Eve can retry across attempts). Bob errors with `InvalidShare { blame: Some(proof) }`, and the processor broadcasts it via `CoordinatorMessage::VerifyBlame` (`processor/src/key_gen.rs:504-541`), publishing `proof.key` to the validator set.

Anyone holding Alice's original ciphertext can now compute `cipher(context, proof.key)` and decrypt Alice's secret share to Bob — the exact side effect the PoP was added to prevent. `blame_internal` will correctly fault Eve (PoP fails inside `decrypt_with_proof`), but the shared key is already public. Repeating this with every sender's message to Bob recovers all of Bob's incoming shares, hence Bob's full secret key share.

### Impact Explanation
Public recovery of a threshold participant's private key share. If shares to a participant from all `n` senders are copied this way (each ciphertext's ECDH key is revealed by an induced blame), the victim's complete secret — and therefore their ability to produce FROST signature shares — is compromised. This is key share recovery reachable by an unprivileged DKG participant using only messages they transmit, matching the "key share recovery" acceptance criterion.

### Likelihood Explanation
Requires only that an attacker participant can (a) observe other senders' `EncryptedMessage`s to a victim, which the DKG dissemination (coordinator-mediated/authenticated broadcast) exposes, and (b) submit a mutated `EncryptedMessage` to the victim. The non-canonical-scalar trigger succeeds with high probability per attempt and is retryable across DKG attempts. The victim's error path publishes the blame proof automatically as part of the standard VerifyBlame flow. No validator misbehavior, collusion, or broken BFT is needed — the attacker is an ordinary DKG participant.

### Recommendation
Do not release an `EncryptionKeyProof` until the message's PoP has been verified. Concretely, in `KeyMachine::calculate_share` (crypto/dkg/pedpop/src/lib.rs:476-499), defer attaching `blame` until after `batch.verify_with_vartime_blame()` confirms the `BatchId::Decryption(l)` PoP — e.g., store the proofs and the deserialized-share flags, run the batch verification first (attributing `Decryption` failures to the sender with `blame: None`), and only then convert decryption/scalar/share failures into `InvalidShare { blame: Some(..) }` for messages whose PoP passed. This restores the invariant documented at `encryption.rs:84-90` that a blame proof can never reveal a shared key for a message whose key the sender did not provably own.

### Proof of Concept
1. Run a PedPoP DKG with participants Eve, Alice, Bob.
2. Alice sends Bob `msg_A = EncryptedMessage { key: kA·G, pop: sig_A, msg: enc_A }` where `enc_A = cipher(ctx, ecdh(kA, enc_pub_B))` of a valid `SecretShare`.
3. Eve sends Bob `msg_E = EncryptedMessage { key: msg_A.key, pop: msg_A.pop, msg: random_bytes }` as her own share message.
4. Bob's `calculate_share` → `decrypt` returns `proof_E` with `key = enc_priv_B · kA·G`; the garbage plaintext fails `C::F::from_repr`, triggering the early `InvalidShare { blame: Some(proof_E) }` return before `batch.verify_with_vartime_blame()` validates `pop`.
5. Bob broadcasts `VerifyBlame` containing `proof_E`; every validator now holds `key = enc_priv_B · msg_A.key`, computes `cipher(ctx, key)`, and decrypts `enc_A`, learning Alice's secret share to Bob.
6. Repeating steps 3–5 for every sender's ciphertext to Bob yields `Σ shares = secret_B`, Bob's full private key share; Eve is blamed (correctly), but the key material is already public — blame is punitive, not restorative.