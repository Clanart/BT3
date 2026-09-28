### Title
ECDH decryption key released for blame before the per-message proof-of-possession is verified, letting a malicious DKG participant decrypt honest shares - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
CVE-2022-4054 is a "secret delivered to an attacker-redirected endpoint" bug: GitLab sent the webhook secret token to whatever URL a maintainer configured, so pointing the destination at an attacker endpoint leaked the secret. Serai's PedPoP DKG has the same shape: `KeyMachine::calculate_share` returns an `EncryptionKeyProof` — which reveals the ECDH shared key `enc_key * msg.key` for that message — as `blame` when a decrypted share fails `C::F::from_repr`. This early error return happens *before* the batch verification that would have checked the message's Schnorr proof-of-possession (`pop`). The PoP exists precisely to prevent an attacker from co-opting an honest message's per-message key and using the blame mechanism to reveal the honest ECDH shared key (the code comments describe this attack at `crypto/dkg/pedpop/src/encryption.rs:83-90`). The early-return path bypasses that defense.

### Finding Description
`Encryption::decrypt` queues the PoP into a `BatchVerifier` under `BatchId::Decryption(l)` but immediately computes and returns `(plaintext, EncryptionKeyProof { key: ecdh(enc_key, msg.key), dleq })` (`crypto/dkg/pedpop/src/encryption.rs:469-501`). In `KeyMachine::calculate_share` (`crypto/dkg/pedpop/src/lib.rs:476-499`):

```rust
let (mut share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
let share =
  Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
    PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
  })?);
```

If `from_repr` fails, the function returns `InvalidShare { blame: Some(blame) }` immediately via `?`, and `batch.verify_with_vartime_blame()` at line 493 is never reached for that message. A `Some(blame)` result is the signal for the caller to publish the `EncryptionKeyProof`, which everyone can use via `Decryption::decrypt_with_proof` (`encryption.rs:366-397`) to decrypt the original ciphertext. Note `decrypt_with_proof` checks the DLEq and the PoP against the *published message*, so the published artifact is self-consistent — the vulnerability is that the proof is generated/released for a message the sender never validly committed to.

Contrast with the intended design: on the batch-failure path, a `BatchId::Decryption(l)` failure (bad PoP) maps to `blame: None` (lines 493-498), correctly withholding the ECDH key. The `from_repr` early-return path skips that discrimination entirely.

### Impact Explanation
An attacker Eve participating in a DKG copies `msg.key` (`X = xG`) from an honest sender Alice's `EncryptedMessage` addressed to victim Bob (per-message keys and ciphertexts are relayed to the coordinator/all validators in the deployment layer, e.g. `processor/src/key_gen.rs`). Eve sends Bob a forged `EncryptedMessage` with `key = X`, arbitrary ciphertext, and an invalid/missing PoP. Bob's `decrypt` yields garbage bytes; `C::F::from_repr` rejects them with high probability per attempt (a random 32-byte string is non-canonical ~7/8 of the time for 255-bit scalar fields; retryable across attempts). Bob's machine returns `InvalidShare { blame: Some(proof) }`, causing Bob to publish `proof.key = b·X` (Bob's static encryption key times Alice's per-message key). Anyone can then derive the ChaCha20 cipher for that ECDH point (`cipher()` uses only `context` and the shared point, `encryption.rs:101-133`) and decrypt Alice's secret share to Bob. Repeating per sender leaks all of Bob's incoming shares, recovering Bob's full secret key share; across `t` victims this recovers the group private key. This is key-share recovery reachable by an unprivileged DKG participant — the same "secrets disclosed to an attacker-chosen channel" class as CVE-2022-4054, here the redirect target is the blame publication channel.

### Likelihood Explanation
Requires only a malicious DKG participant who can observe honest `EncryptedMessage`s (they are broadcast/relayed through the coordinator in the real deployment) and cause the victim to run `calculate_share`. Success per message is probabilistic (~7/8) but trivially repeatable across DKG attempts since the attacker fully controls the forged ciphertext. The only mitigating factor is that the victim must still be willing to run the DKG with the attacker, which is inherent to the protocol.

### Recommendation
Do not emit an `EncryptionKeyProof` until the message's PoP has been verified. Concretely, in `KeyMachine::calculate_share`, gate the `from_repr` failure blame on PoP validity — e.g., verify the PoP synchronously before attempting deserialization (or track which proofs were PoP-verified and only attach `Some(blame)` for those). Alternatively, split `Encryption::decrypt` so the `EncryptionKeyProof` is only produced/returned after `batch.verify_with_vartime_blame()` confirms the `BatchId::Decryption(l)` statement, which is the ordering the blame system already assumes.

### Proof of Concept
1. Setup a PedPoP `KeyGenMachine` DKG (n ≥ 2) with honest Alice (a) and Bob (b), and attacker Eve (e). Complete round 1 normally so Bob registers all `enc_key`s.
2. Alice produces `EncryptedMessage { key: X, pop, msg }` destined for Bob; Eve records `X`.
3. Eve constructs `EncryptedMessage { key: X, pop: <arbitrary invalid SchnorrSignature>, msg: <32 arbitrary bytes> }` and sends it to Bob as Eve's share message.
4. Bob calls `calculate_share`. `decrypt` returns garbage plaintext plus `EncryptionKeyProof { key: b·X, dleq }`. If `from_repr` fails (dominant case; retry otherwise), Bob's machine returns `PedPoPError::InvalidShare { participant: e, blame: Some(proof) }` before `batch.verify_with_vartime_blame()` runs.
5. Bob publishes `proof` for blame. Eve (and everyone) runs `cipher(context, &proof.key)` and XORs it against Alice's `msg` ciphertext, recovering Alice's secret share to Bob — without Eve ever knowing `x = log(X)` or Bob's `enc_key`, exactly the attack the PoP comment in `encryption.rs` claims to prevent.