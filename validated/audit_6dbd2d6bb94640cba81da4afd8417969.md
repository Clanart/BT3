### Title
Blame proof issued before PoP verification leaks a co-opted message's ECDH key, revealing another participant's secret share - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
CVE-2020-3996 concerns improper management of identifiers leading to information leakage to unauthorized users. The Serai analog lives in PedPoP's blame flow: `KeyMachine::calculate_share` returns an `EncryptionKeyProof` (which reveals the ECDH shared key for a message) when the decrypted share is a non-canonical scalar — *before* the message's Schnorr proof-of-possession has been batch-verified. The code comments in `encryption.rs` explicitly describe this exact attack (reusing Alice's per-message key `X` so Bob's blame reveals `bX`, leaking Alice's message) and claim the PoP prevents it — but the early `?` return bypasses the PoP check entirely.

### Finding Description
In `crypto/dkg/pedpop/src/lib.rs:476-499`, `calculate_share` loops over received `EncryptedMessage`s:

```rust
let (mut share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
let share =
  Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
    PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
  })?);
```

`Encryption::decrypt` (`crypto/dkg/pedpop/src/encryption.rs:469-501`) only *queues* the PoP into the `BatchVerifier` under `BatchId::Decryption(l)`; it returns the plaintext plus a freshly computed `EncryptionKeyProof` containing `key = ecdh(enc_key, msg.key) = b·msg.key`. When `from_repr` fails (decrypted bytes are not a canonical scalar), the function returns `InvalidShare` carrying `Some(blame)` at line 481 — and `batch.verify_with_vartime_blame()` at line 493 is never reached, so the invalid/absent PoP is never detected.

The threat model is documented in `encryption.rs:84-88`: "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X. While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob." The PoP exists to stop this, but this ordering bug reintroduces it.

Attack:
1. Eve observes Alice's `EncryptedMessage` to Bob and copies its `key` field `X` (a public value).
2. Eve sends Bob `EncryptedMessage { key: X, pop: <invalid>, msg: <arbitrary 32 bytes> }`.
3. Bob's `decrypt` computes `bX`, applies the ChaCha20 keystream, and produces pseudorandom `share_bytes`. For a ~32-byte field, roughly half of all byte strings are non-canonical (e.g. `l ≈ 2^252` for Ristretto/ed25519 → ~1/16 chance per attempt, trivially retried across attempts/messages until one sticks).
4. On a non-canonical result, `calculate_share` early-returns `InvalidShare { participant: Eve, blame: Some(EncryptionKeyProof{ key: bX, dleq }) }`, which Bob publishes as the blame proof per `processor/src/key_gen.rs:419-425`.
5. `bX` is now public. Anyone holding Alice's original `EncryptedMessage` to Bob (which transited the authenticated-but-not-necessarily-private channel) can apply `cipher(context, bX)` and recover Alice's secret share to Bob.

### Impact Explanation
The published `EncryptionKeyProof.key` is the raw ECDH shared point `bX`, and `decrypt_with_proof` (`encryption.rs:392`) shows possession of that point suffices to decrypt the message. Revealing it for a message Eve forged leaks the *unrelated* message Alice sent to Bob — Alice's secret share of her Pedersen polynomial to Bob. Combined with leaked shares from `t-1` other senders' messages (the attack is repeatable per sender/recipient pair since each per-message key `X` is independently co-optable), an attacker who eavesdrops the channel can reconstruct honest participants' polynomial evaluations and potentially recover secret key material of the resulting `ThresholdKeys`. Even a single leaked share violates the DKG's confidentiality guarantee and is exactly the "massive side effect" the PoP was added to prevent.

### Likelihood Explanation
Reachable by any DKG participant (unprivileged in the cryptographic sense — only needs to send a malformed `EncryptedMessage` and observe public traffic). Requires no collusion, no malicious validator assumptions beyond a single faulty sender, and no broken BFT. Success probability per forged message is the probability a random 32-byte string fails `C::F::from_repr` (≈50% for secp256k1's near-2^256 order; ≈1/16 for ristretto255), and Eve can grind across attempts since each DKG attempt accepts one message per participant. The flaw is a pure ordering bug in in-scope production code.

### Recommendation
Do not emit the `EncryptionKeyProof` until the PoP for that message has been verified. Concretely: in `calculate_share`, verify the queued batch (or at least each message's PoP) before converting a non-canonical decryption into `InvalidShare { blame: Some(..) }`; if the PoP fails, return `blame: None` (the `BatchId::Decryption` → `None` path already exists at `lib.rs:495`). Alternatively, treat non-canonical plaintext identically to a failed share check — queue it into the batch so the blame decision happens only after PoP verification, ensuring a proof is never revealed for a message whose per-message key the sender did not actually control.

### Proof of Concept
1. Run a PedPoP DKG (`test_pedpop` harness in `crypto/dkg/pedpop/src/tests.rs` provides `commit_enc_keys_and_shares`).
2. As honest Alice (participant 1), produce shares; capture `share_1to2 = secret_shares[1][2]` and note its `key` field `X` via `EncryptedMessage::read`.
3. As malicious Eve (participant 3), construct `EncryptedMessage::<C, SecretShare<C::F>> { key: X, pop: <arbitrary invalid signature>, msg: SecretShare(<random bytes>) }` and send it to Bob (participant 2) in place of a real share.
4. Bob's `calculate_share` calls `encryption.decrypt`, computing `bX` and keystream-decrypting to pseudorandom bytes. Repeat with fresh random `msg` until `C::F::from_repr(share_bytes.0)` returns `None`.
5. Observe `PedPoPError::InvalidShare { participant: 3, blame: Some(proof) }` where `proof.key == bX` — confirmed by checking `proof.dleq.verify(..., [G, X], [bob_enc_pub, proof.key])` succeeds.
6. Apply `cipher(context, &proof.key)` to Alice's captured `share_1to2.msg`; recover Alice's secret share to Bob — a value Bob never authorized revealing and that Eve's forged message was blamed for leaking.