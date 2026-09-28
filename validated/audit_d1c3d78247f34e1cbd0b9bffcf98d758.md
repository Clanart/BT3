### Title
Invalid-PoP blame path in PedPoP leaks the ECDH key of an unrelated honest encrypted share - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The DKG encryption layer relies on a per-message Schnorr proof-of-possession (`pop`) to stop an attacker (Eve) from claiming another participant's (Alice's) per-message encryption key `X` in a malicious share sent to a victim (Bob). The code's own comments explain that without the PoP, Bob's blame proof would reveal `ecdh(bob_enc_key, X)`, which is exactly the shared key Alice used to encrypt her real secret share to Bob — leaking it. However, in `KeyMachine::calculate_share`, the PoP is only *queued* into a `BatchVerifier` (inside `Encryption::decrypt`) and is never actually verified before the function can return `InvalidShare` with `blame: Some(proof)`. When the decrypted share bytes fail `C::F::from_repr` (non-canonical scalar), the error is returned early via `?`, bypassing `batch.verify_with_vartime_blame()` entirely. An attacker can therefore attach a garbage PoP and non-canonical ciphertext while reusing Alice's `key`, and the victim will emit an `EncryptionKeyProof` that publicly reveals the ECDH shared key for Alice's honest message, decrypting Alice's confidential share to the victim for all observers.

### Finding Description
- `EncryptedMessage` carries `key`, `pop`, and `msg`. The comment at `crypto/dkg/pedpop/src/encryption.rs:83-90` explicitly documents the co-opted-key attack the `pop` exists to prevent: a blame statement revealing `bX` also reveals Alice's message to Bob.
- `Encryption::decrypt` (`encryption.rs:469-501`) queues the PoP verification into a caller-supplied `BatchVerifier` under `BatchId::Decryption(l)`, then unconditionally computes `ecdh(self.enc_key, msg.key)` and returns `(decrypted_msg, EncryptionKeyProof { key, dleq })`. The proof is generated and returned regardless of whether the PoP is valid.
- In `KeyMachine::calculate_share` (`lib.rs:476-499`), for each `(l, share)` the code calls `decrypt`, then immediately does `C::F::from_repr(share_bytes.0).ok_or_else(|| PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) })?`. This early-returns the `EncryptionKeyProof` *before* `batch.verify_with_vartime_blame()` on line 493 is ever reached, so an invalid `pop` is never detected on this path.
- Note the subtlety: if the decrypted bytes *were* a canonical scalar, the share statement would be queued after the decryption statement, and `blame_vartime` (which returns the leftmost invalid statement, `crypto/multiexp/src/batch.rs:108-123`) would return `BatchId::Decryption(l)` → `blame: None`. The reliable attack path is therefore specifically a ciphertext that decrypts to a non-canonical scalar — trivially achieved since the attacker cannot predict the keystream and ~all random encodings are non-canonical (for ed25519/secp256k1/ristretto scalar fields, a random 32-byte string is non-canonical with probability ≈ 1 − ℓ/2^256; even simpler, the victim decrypts with the *wrong* key: `ecdh(bob, alice_X)` applied to attacker bytes is uniformly random).
- Deployment reachability: `processor/src/key_gen.rs:416-425` propagates `PedPoPError::InvalidShare { participant, blame }` into `ProcessorMessage::InvalidShare` carrying the serialized blame proof, and `VerifyBlame` (`key_gen.rs:504-563`) publishes that proof to the `AdditionalBlameMachine`, making the revealed ECDH key part of the blame record.

### Impact Explanation
The revealed `EncryptionKeyProof.key` equals `ecdh(victim_enc_key, X)` where `X` is Alice's per-message key for her genuine share to the victim. Anyone holding it can run `cipher(context, key).apply_keystream` on Alice's broadcast/relayed `EncryptedMessage` and recover `f_alice(victim)` — Alice's Pedersen secret share destined for that participant. This is exactly the confidentiality break the `pop` was added to prevent ("This is a massive side effect which could break some protocols," `encryption.rs:87-88`). It leaks honest-party DKG secret material to third parties, degrading the threshold assumption (a leaked share plus other compromises reduces the effective threshold), and can be repeated against multiple victims in one DKG session. Severity: Medium — secret disclosure of a key-share component reachable solely by a protocol participant sending crafted public messages; not full group-key recovery on its own.

### Likelihood Explanation
Highly reliable once a DKG session runs: the attacker needs only observe a victim's peer's `EncryptedMessage` (to copy its `key` field), substitute it into their own share message to that victim with an arbitrary `pop` and arbitrary ciphertext (e.g., 0xFF…FF per `invalidate_share_serialization`, `encryption.rs:218-239`), and the victim's `calculate_share` returns the revealing blame proof with probability ≈ 1, with no PoP verification gating it. The only requirement is participating in one PedPoP DKG round — no threshold collusion, no timing, no privileged access.

### Recommendation
Verify the PoP before producing or returning the `EncryptionKeyProof`. Concretely, in `calculate_share`, do not attach `blame` until `batch.verify_with_vartime_blame()` has run and identified the failure as `BatchId::Share(l)`; the non-canonical `from_repr` failure path must first check the queued `Decryption(l)` statement (e.g., run the batch verification eagerly per sender, or check `msg.pop.verify` synchronously inside `Encryption::decrypt` and return `blame: None` / `DecryptionError::InvalidSignature` when it fails, mirroring `decrypt_with_proof` at `encryption.rs:374-379` which correctly checks the PoP *before* accepting a proof).

### Proof of Concept
```rust
// Setup: participants 1..=n run PedPoP. Eve = participant E, victim = participant B,
// honest sender = participant A.
// 1. A -> B: EncryptedMessage { key: X, pop: valid_pop, msg: enc(f_A(B)) } (observed by Eve).
// 2. Eve constructs her share message to B:
let mut evil = EncryptedMessage::<C, SecretShare<C::F>>::read(
    &mut eve_bytes.as_ref(), params,
).unwrap();
evil.key = alice_msg_to_bob.key;              // claim Alice's per-message key X
evil.pop = SchnorrSignature { R: random_point, s: random_scalar }; // invalid PoP
evil.msg.as_mut().as_mut().fill(0xFF);        // ciphertext -> non-canonical scalar after
                                             // decryption under ecdh(bob, X)
// 3. B runs:
let res = key_machine_bob.calculate_share(&mut rng, shares_incl_evil);
// 4. res == Err(PedPoPError::InvalidShare { participant: E,
//        blame: Some(EncryptionKeyProof { key: ecdh(bob_enc, X), dleq }) })
//    The PoP batch statement was queued but never verified (early return before
//    batch.verify_with_vartime_blame()).
// 5. Anyone with the published blame proof runs:
//    cipher::<C>(context, &proof.key).apply_keystream(alice_msg_to_bob.msg.as_mut())
//    -> recovers f_A(B), Alice's secret share to Bob.
```
Key lines proving root cause: `crypto/dkg/pedpop/src/lib.rs:476-499` (early `?` return before line-493 batch verification) and `crypto/dkg/pedpop/src/encryption.rs:469-501` (proof generated unconditionally, PoP only queued), against the documented mitigation intent at `encryption.rs:83-90`.