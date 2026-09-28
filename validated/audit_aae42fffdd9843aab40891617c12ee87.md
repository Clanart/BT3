### Title
Blame proof (ECDH shared key) is emitted before the per-message proof-of-possession is verified, letting a malicious DKG sender co-opt another participant's encryption key and leak their secret share to everyone - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` returns an `EncryptionKeyProof` (which publicly reveals the ECDH shared key `enc_key * msg.key`) as soon as the decrypted share bytes fail `C::F::from_repr`. The Schnorr proof-of-possession on `msg.key` is only queued into a `BatchVerifier` that is never verified on this early-return path. This reintroduces exactly the key-co-option attack the PoP was added to prevent (see the comment in `crypto/dkg/pedpop/src/encryption.rs` and `spec/cryptography/Distributed Key Generation.md`), causing Alice's encrypted share to Bob to become publicly decryptable by all participants — disclosure of threshold secret-share material to unauthorized parties, analogous to CVE-2020-10975's disclosure of protected vulnerability metadata.

### Finding Description
`Encryption::decrypt` only *queues* the PoP check via `msg.pop.batch_verify(...)` and then immediately computes the blame material:

```rust
// crypto/dkg/pedpop/src/encryption.rs:479-499
msg.pop.batch_verify(rng, batch, batch_id, msg.key, pop_challenge::<C>(...));
let key = ecdh::<C>(&self.enc_key, msg.key);
cipher::<C>(self.context, &key).apply_keystream(msg.msg.as_mut().as_mut());
(msg.msg, EncryptionKeyProof { key, dleq: DLEqProof::prove(...) })
```

In `calculate_share` (`crypto/dkg/pedpop/src/lib.rs:476-499`), the blame proof is returned with `blame: Some(...)` via `ok_or_else`/`?` when the decrypted bytes are not a canonical scalar — **before** `batch.verify_with_vartime_blame()` runs. The only paths that check the batch are the subsequent `batch.verify_with_vartime_blame()` at line 493, which is unreachable once the non-canonical-scalar error fires:

```rust
let share = Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0))
  .ok_or_else(|| PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) })?);
```

The comment in `encryption.rs:84-88` describes the prevented attack: "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X. While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob." The PoP is the sole mitigation — and it is bypassed on this error path, since `decrypt_with_proof`/`blame` only consume the already-published `EncryptionKeyProof.key`, and `ProcessorMessage::InvalidShare` propagates `blame.serialize()` publicly (`processor/src/key_gen.rs:419-425`).

### Impact Explanation
The published `EncryptionKeyProof.key` is the point `bX` (Bob's private enc_key times the per-message public key X). Anyone holding Alice's `EncryptedMessage` to Bob — which used the same X — can compute `cipher(context, bX)` and fully decrypt Alice's secret share to Bob. Repeating this for every honest sender's message to a single victim leaks enough shares to reconstruct the victim's entire threshold secret share (`self.secret` is the plain sum of received shares, `lib.rs:484`) to all n participants and to Eve. This is key-share disclosure to unauthorized parties; even where the aborted DKG discards the keys, the leak violates the confidentiality guarantee the encryption layer exists to provide.

### Likelihood Explanation
Eve must send Bob an `EncryptedMessage` whose `key` field equals Alice's observed per-message key X. Eve does not know the discrete log of X, so she cannot produce a valid PoP or control the plaintext — but Bob decrypts with `bX` regardless, and random ChaCha20 output is a non-canonical scalar with probability ≈ 1 − l/2^256 (≈ 50% for 255-bit groups like Ristretto/secp256k1). Each invalid PoP attempt that lands on the non-canonical path emits the blame proof; nothing rate-limits or authenticates the per-message key binding beyond the skipped PoP. The attack is reachable by any DKG participant with only public protocol messages.

### Recommendation
Do not attach an `EncryptionKeyProof` until the PoP has actually been verified. Options: (a) verify `msg.pop` synchronously inside `Encryption::decrypt` (or before producing the proof) and return `blame: None` on PoP failure; (b) restructure `calculate_share` so non-canonical-share errors are deferred until after `batch.verify_with_vartime_blame()`, mapping a `BatchId::Decryption(l)` failure to `blame: None` and only attaching `blames[l]` once the PoP is confirmed; (c) check PoP inside `decrypt_with_proof`/`blame_internal` *and* suppress proof emission upstream — note `decrypt_with_proof` does verify the PoP before using `proof.key` (`encryption.rs:374-390`), so the leak occurs purely because the raw `proof.key` is exposed in the serialized blame regardless of PoP validity.

### Proof of Concept
1. Participants run `KeyGenMachine::generate_coefficients` / `SecretShareMachine::generate_secret_shares`. Alice's machine produces `EncryptedMessage { key: X, pop, msg }` for Bob (`encrypt` in `encryption.rs:135-168`).
2. Eve observes X and sends Bob `EncryptedMessage { key: X, pop: <arbitrary validly-encoded signature>, msg: <random 32-byte ciphertext> }` as her share to Bob.
3. Bob's `calculate_share`: `self.encryption.decrypt` queues the PoP (unverified), computes `key = bX`, decrypts to garbage. With ~50% probability the garbage is not a canonical `C::F`, triggering `PedPoPError::InvalidShare { participant: eve, blame: Some(EncryptionKeyProof { key: bX, dleq }) }` — returned before `batch.verify_with_vartime_blame()` runs (lib.rs:480-482).
4. Bob publishes `blame.serialize()` via `ProcessorMessage::InvalidShare` (processor/src/key_gen.rs:419-425).
5. Eve (and every participant running `AdditionalBlameMachine::blame`/`VerifyBlame`) now has `bX` and decrypts Alice's original share: `cipher(context, bX).apply_keystream(alice_msg.msg)`.
6. Repeat for each sender's message to Bob → all of Bob's received shares are public → Bob's `ThresholdKeys` secret share is recoverable by unauthorized parties.

The existing test `invalid_share_serialization_blame` (`crypto/dkg/pedpop/src/tests.rs:283-313`) already exercises this exact code path (blame emitted on non-canonical deserialization), confirming reachability.