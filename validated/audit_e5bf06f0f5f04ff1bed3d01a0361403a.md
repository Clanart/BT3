### Title
Early blame-proof return in `KeyMachine::calculate_share` bypasses proof-of-possession verification, leaking the ECDH share-decryption key for an attacker's chosen `msg.key` — (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
CVE-2023-37579 is an incorrect-authorization bug: a function returns sensitive configuration (credentials) to a caller without checking they are authorized for that object. The analog in Serai is a missing-check disclosure of a decryption credential: `KeyMachine::calculate_share` returns an `EncryptionKeyProof` (the ECDH shared point that decrypts a PedPoP share ciphertext) as blame for a message whose Schnorr proof-of-possession was only *queued* for batch verification, never verified. A sender who reuses another party's ephemeral key field can force an honest recipient to emit a blame proof revealing the ECDH key that decrypts the victim's secret share.

### Finding Description
`EncryptedMessage` carries a Schnorr PoP over the per-message ephemeral key `key` precisely because "Bob would then use this to create a blame argument against Eve... they'd reveal bX, revealing Alice's message to Bob" — the PoP exists to stop an attacker from getting the recipient to publish `enc_key * msg.key` for a `msg.key` the attacker does not own (`crypto/dkg/pedpop/src/encryption.rs:83-91`).

However, in `Encryption::decrypt`, the PoP is only enqueued via `batch_verify`, while `ecdh(&self.enc_key, msg.key)` is computed and returned inside `EncryptionKeyProof` immediately (`crypto/dkg/pedpop/src/encryption.rs:479-500`).

In `KeyMachine::calculate_share`, the deserialized share bytes are checked with `C::F::from_repr`, and on failure the function returns `PedPoPError::InvalidShare { blame: Some(blame) }` **before** `batch.verify_with_vartime_blame()` is ever called (`crypto/dkg/pedpop/src/lib.rs:476-499`). The eager `?` on line 482 exits the function, so the queued `BatchId::Decryption(l)` PoP check is dropped unverified.

An attacker therefore sends an `EncryptedMessage` where:
- `key` = a copied ephemeral key `X` taken from victim Alice's `EncryptedMessage` addressed to the same recipient (the field is transmitted in the clear, `encryption.rs:179-183`),
- `pop` = any garbage signature (it will never be checked),
- `msg` = bytes that fail `C::F::from_repr` (e.g., all `0xFF`).

The honest recipient's `calculate_share` returns `blame: Some(EncryptionKeyProof { key: b·X, dleq })`. When that blame is published (its explicit purpose), `b·X` is exactly the ECDH shared key for Alice's ciphertext to this recipient; `decrypt_with_proof` / `cipher` show `b·X` is all that is needed to re-key `ChaCha20` and decrypt Alice's `SecretShare` (`encryption.rs:101-133, 381-393`).

### Impact Explanation
The published blame proof decrypts the victim participant's PedPoP secret share, which is an additive component of every recipient's FROST `secret_share` (`lib.rs:484`). If the attacker induces this leak against enough senders (or collects shares via repeated DKG attempts), they recover enough shares to reconstruct the threshold secret key — direct key-share recovery, a High/Critical-severity outcome. The PoP defense documented as preventing exactly this side effect is silently inoperative on the deserialization-failure path.

### Likelihood Explanation
The attack requires only being a DKG participant able to send crafted `EncryptedMessage` bytes to `calculate_share` — an unprivileged input explicitly in scope. It is deterministic: a non-canonical scalar payload plus a copied `key` field triggers the early return every time, and the leaked `b·X` unconditionally decrypts the victim's share ciphertext. The only requirement is observing a victim's `EncryptedMessage` to the same recipient, which travels over the (authenticated but not secret-from-participants) DKG channel.

### Recommendation
Verify the PoP before releasing any blame material. Options:

- In `Encryption::decrypt`, verify `msg.pop` synchronously (or split decryption into a verify step and a key-derivation step) so no `EncryptionKeyProof` exists for a message with an invalid PoP.
- In `calculate_share`, defer emitting `blame` until after `batch.verify_with_vartime_blame()` confirms the `BatchId::Decryption(l)` statement; on any deserialization failure, run the batch first and only attach `Some(blame)` if the PoP for that sender passed.
- Additionally reject reuse: track seen `msg.key` values per context and refuse to produce proofs for a `key` already seen attached to a different sender.

### Proof of Concept
```rust
// Recipient r runs PedPoP with sender set {alice, eve, ...}.
// 1. Alice sends EncryptedMessage { key: X, pop: sig_A, msg: Enc(b·X, share_A) } to r.
// 2. Eve observes X (plaintext field in the serialized message).
// 3. Eve builds her own EncryptedMessage to r:
let mut forged = EncryptedMessage::<C, SecretShare<C::F>>::read(
    &mut bytes_with(X, garbage_pop, noncanonical_scalar_payload).as_ref(),
    params,
).unwrap();
//    key = X, pop = invalid SchnorrSignature, msg = [0xFF; 32] (fails F::from_repr)
// 4. r calls KeyMachine::calculate_share(rng, shares_including_eve);
//    - decrypt() queues Eve's PoP for batch verification AND computes proof.key = b·X
//    - from_repr fails -> returns Err(InvalidShare { participant: eve, blame: Some(proof) })
//      WITHOUT calling batch.verify_with_vartime_blame()
// 5. r publishes `proof` as blame against Eve. Anyone can now:
let mut alice_ct = alice_msg.clone(); // EncryptedMessage { key: X, .. }
cipher::<C>(context, &proof.key).apply_keystream(alice_ct.msg.as_mut().as_mut());
// alice_ct.msg now holds Alice's plaintext SecretShare -> share recovered.
// The DLEq in `proof` even verifies against X and r's enc_key, so the leak is
// publicly attributable and cannot be dismissed as a forgery.
```

Root cause is the ordering at `crypto/dkg/pedpop/src/lib.rs:480-482` returning `blame` before the PoP batch at `lib.rs:493` is verified, combined with `decrypt` producing the ECDH proof unconditionally at `crypto/dkg/pedpop/src/encryption.rs:487-499`.