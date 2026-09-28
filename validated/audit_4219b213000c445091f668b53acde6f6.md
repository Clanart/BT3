### Title
Blame proof released before PoP verification leaks honest senders' ECDH keys and secret shares - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` returns `PedPoPError::InvalidShare { participant, blame: Some(blame) }` — containing the `EncryptionKeyProof` that reveals the ECDH shared key for a message — as soon as the decrypted bytes fail scalar deserialization, *before* the batched proof-of-possession on `msg.key` has been verified. An unprivileged participant can therefore replay an honest sender's per-message encryption key with a forged (PoP-invalid) ciphertext, trigger the error path, and obtain a blame proof that decrypts the honest party's real secret share. This is exactly the attack the PoP in `EncryptedMessage` was added to prevent; the mitigation is bypassed by error-ordering.

### Finding Description
In `crypto/dkg/pedpop/src/lib.rs:477-499`, `calculate_share` iterates received shares and calls `encryption.decrypt`, which unconditionally computes `key = ecdh(&self.enc_key, msg.key)` and returns an `EncryptionKeyProof` attesting to that shared key (`crypto/dkg/pedpop/src/encryption.rs:487-500`). The PoP Schnorr signature is only *queued* into the `BatchVerifier` (`encryption.rs:479-485`) and checked later at `lib.rs:493` by `batch.verify_with_vartime_blame()`.

However, at `lib.rs:479-482`, if the decrypted bytes are not a canonical scalar, the function returns early:

```rust
let share = Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0))
  .ok_or_else(|| PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) })?);
```

This error propagates immediately — the batch (and hence the PoP) is never verified — yet it already hands the caller a usable blame proof revealing `key = enc_key_recipient * msg.key`.

The PoP exists precisely to stop this: `encryption.rs:84-90` documents that "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X... Bob would then use this to create a blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob." The defense only holds if no blame material is emitted for a message whose PoP is invalid — an assumption violated by the early return.

### Impact Explanation
Eve (a DKG participant) copies the per-message key `X = g*x` from Alice's authenticated `EncryptedMessage` to Bob, attaches an invalid PoP and a ciphertext that decrypts to non-canonical bytes, and sends it as her own share to Bob. Bob's `calculate_share` fails `from_repr` and returns `InvalidShare { blame: Some(proof) }` where `proof.key = b*X` is Alice↔Bob's ECDH key. When Bob publishes the blame proof (the protocol's intended behavior, e.g. `processor/src/key_gen.rs:419-425` serializes it into `ProcessorMessage::InvalidShare`), anyone can run `Decryption::decrypt_with_proof` (`encryption.rs:366-397`) to recover Alice's secret share to Bob. Repeated across participants, an attacker can collect enough shares to reconstruct an honest dealer's polynomial/secret contribution, undermining the DKG's confidentiality and enabling key-share recovery.

### Likelihood Explanation
Requires only a protocol participant who can observe one authenticated ciphertext and inject one crafted `EncryptedMessage` — no collusion, no broken primitives. The trigger is deterministic: any ciphertext decrypting to a non-canonical scalar hits the early return. The only requirement is that the victim publishes the blame proof, which the protocol expects honest nodes to do.

### Recommendation
Do not attach `blame` to the deserialization-failure error until the PoP has been verified. Either verify `msg.pop` synchronously inside `decrypt` (or before `from_repr`) and return `InvalidShare { participant: l, blame: None }` on PoP failure — matching the `BatchId::Decryption` path that already maps PoP failures to `blame: None` at `lib.rs:495` — or defer constructing/returning any `EncryptionKeyProof` until after `batch.verify_with_vartime_blame()` confirms the decryption-batch statements.

### Proof of Concept
1. Alice sends Bob `Enc_{bX}(share)` with `key = X`, a valid PoP signed by Alice.
2. Eve registers her own encryption key with Bob, then sends Bob `EncryptedMessage { key: X, pop: <invalid signature>, msg: garbage }`.
3. Bob calls `calculate_share`. `decrypt` computes `key = b*X`, builds `EncryptionKeyProof { key: bX, dleq }`, decrypts `msg` to non-scalar bytes.
4. `C::F::from_repr` fails at `lib.rs:480` → returns `PedPoPError::InvalidShare { participant: Eve, blame: Some(proof) }` before `batch.verify_with_vartime_blame()` ever runs, so Eve's invalid PoP is never checked.
5. Bob publishes `proof`. Anyone executes `cipher(context, &proof.key).apply_keystream` on Alice's original ciphertext, recovering Alice's secret share to Bob — the disclosure the PoP was designed to prevent (`encryption.rs:86-88`).