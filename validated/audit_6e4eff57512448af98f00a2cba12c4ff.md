### Title
Blame proof emitted before proof-of-possession verification leaks the ECDH key for a co-opted message key, decrypting another participant's confidential share - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP DKG encrypts each secret share under a per-message key and an ECDH shared secret with the recipient's encryption key. To prevent an attacker from co-opting someone else's per-message key and thereby triggering a blame proof that reveals the victim's ECDH shared key, every `EncryptedMessage` carries a Schnorr proof-of-possession (`pop`) over the message key. However, in `KeyMachine::calculate_share`, when a decrypted share fails scalar deserialization, the function returns `InvalidShare` with `blame: Some(proof)` *immediately*, before the batched PoP verification (`batch.verify_with_vartime_blame`) has run. An unprivileged participant can therefore copy victim Alice's message key into their own `EncryptedMessage`, supply an invalid PoP and non-canonical share bytes, and receive a valid `EncryptionKeyProof` revealing `ecdh(enc_key_bob, key_alice)` — which decrypts Alice's confidential share to Bob.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, `Encryption::decrypt` queues the PoP for *batch* verification but unconditionally computes the ECDH key and returns an `EncryptionKeyProof` containing that shared key (lines 479–500). The correctness of this design depends on the caller never releasing the proof unless the batch verifies — and the code acknowledges this: a `BatchId::Decryption(l)` failure maps to `blame: None` in `crypto/dkg/pedpop/src/lib.rs` (lines 493–498).

But lines 476–482 break that invariant:

```rust
let (mut share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
let share =
  Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
    PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
  })?);
```

The `?` on `from_repr` failure returns `InvalidShare { blame: Some(blame) }` — publishing the ECDH shared key — while the PoP for that message is still an unverified entry in `batch`, which is then dropped when the function early-returns. The invalid-PoP → `blame: None` guard in the `map_err` handler is never reached.

The PoP challenge binds `context`, `nonce`, `key`, `sender`, and `msg` (`pop_challenge`, encryption.rs lines 302–324), so the attacker cannot produce a valid PoP for Alice's key (they don't know its discrete log) — and they don't need to, because the leak occurs before verification.

This is precisely the attack the PoP was added to prevent, per the in-code threat analysis at encryption.rs lines 84–90: "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X… Bob would then use this to create a blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob."

### Impact Explanation
`EncryptionKeyProof.key` is the raw ECDH shared point, and `decrypt_with_proof` (encryption.rs lines 381–393) shows it directly drives `cipher::<C>(context, &proof.key)` to decrypt. Once the blame proof is published (the documented blame workflow has the accuser broadcast it so all participants can verify fault), every participant can derive the ChaCha20 keystream and decrypt Alice's `EncryptedMessage<SecretShare>` to Bob. This discloses a confidential DKG secret share — an unauthorized cross-party disclosure of confidential protocol data via improper sequencing of an authorization check, directly analogous to the GitLab report of confidential data exposed due to an access check applied in the wrong place. Exposure of individual polynomial evaluations weakens the assumption that shares remain private to their (sender, recipient) pair and, combined with other share disclosures, erodes the threshold security margin.

### Likelihood Explanation
Reachable by any DKG participant (unprivileged, public inputs only): they observe Alice's broadcast/p2p `EncryptedMessage` in round 2, extract `msg.key`, construct their own `EncryptedMessage` to Bob with the same `key`, arbitrary invalid `pop` bytes, and a `msg` buffer that fails `C::F::from_repr` (e.g., all-`0xFF` bytes on a non-255-prime field). Bob's `calculate_share` returns `Some(EncryptionKeyProof)` deterministically. No collusion, no privileged access, and no protocol deviation beyond sending a malformed share — which the protocol already anticipates and handles via blame.

### Recommendation
Do not release the blame proof before PoP verification. In `calculate_share`, defer the `from_repr` deserialization check until after `batch.verify_with_vartime_blame()` succeeds, or perform an inline `msg.pop.verify(...)` before attaching `Some(blame)` to the early error return. Alternatively, have `decrypt` return the proof only behind a token that is unlocked once the batch verifies. A regression test should assert that an `EncryptedMessage` with a re-used foreign `key`, invalid PoP, and non-canonical plaintext yields `InvalidShare { blame: None }`.

### Proof of Concept
1. Run PedPoP with `n ≥ 3`, `t ≥ 2`. Alice (participant A) completes `generate_secret_shares`; her `EncryptedMessage` to Bob (participant B) has per-message public key `K_A`.
2. Eve (participant E) builds `EncryptedMessage { key: K_A, pop: <invalid SchnorrSignature>, msg: SecretShare([0xFF; 32]) }` and sends it to Bob as her round-2 share.
3. Bob calls `KeyMachine::calculate_share`. `encryption.decrypt` computes `shared = enc_key_B * K_A`, queues the (invalid) PoP into `batch`, and returns `(garbage_bytes, EncryptionKeyProof { key: shared, dleq })`.
4. `C::F::from_repr(garbage)` fails → `PedPoPError::InvalidShare { participant: E, blame: Some(proof) }` is returned; `batch` (containing the PoP that would have failed) is dropped without verification.
5. Bob publishes the blame proof per the blame protocol. Anyone computes `cipher(context, proof.key)` and applies its keystream to Alice's original ciphertext `msg`, recovering Alice's secret share to Bob in the clear.

Root cause confirmed at `crypto/dkg/pedpop/src/lib.rs:476-482` (early return with `Some(blame)` before `batch.verify_with_vartime_blame` at line 493) and `crypto/dkg/pedpop/src/encryption.rs:479-500` (proof computed unconditionally, PoP only queued).