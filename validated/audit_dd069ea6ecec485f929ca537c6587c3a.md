### Title
Blame ECDH proof is computed and returned before the PoP authenticating `msg.key` is verified, enabling leak of an honest party's secret share - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs), [File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
Analogous to CVE-2023-26463 (a value used for a second purpose after an incomplete/incorrect access check), `KeyMachine::calculate_share` decrypts each incoming `EncryptedMessage` and immediately obtains an `EncryptionKeyProof` containing the ECDH shared key, while the proof-of-possession (PoP) on `msg.key` is only *queued* into a `BatchVerifier` and verified later. If the decrypted bytes fail scalar deserialization, the function early-returns `PedPoPError::InvalidShare { blame: Some(blame) }` before `batch.verify_with_vartime_blame()` ever runs — so the ECDH key is publishable even though the PoP that binds `msg.key` to its claimed sender was never checked.

### Finding Description
In `Encryption::decrypt` (`crypto/dkg/pedpop/src/encryption.rs:469-500`), the Schnorr PoP is queued via `msg.pop.batch_verify(...)`, then `key = ecdh(&self.enc_key, msg.key)` is computed and returned inside `EncryptionKeyProof` unconditionally. In `KeyMachine::calculate_share` (`crypto/dkg/pedpop/src/lib.rs:476-499`), if `C::F::from_repr(share_bytes.0)` fails, the function returns early at line 481-482 with `blame: Some(blame.clone())`, skipping `batch.verify_with_vartime_blame()` at line 493 entirely.

The `pop_challenge` binds `sender`, `key`, `nonce`, and `msg` (`encryption.rs:302-324`), and the spec/comments state the PoP exists precisely to stop an attacker from co-opting someone else's per-message key so that a blame proof reveals a third party's message (`encryption.rs:83-91`). That defense is only enforced inside `batch.verify_with_vartime_blame()` — which is bypassed on the early-error path.

### Impact Explanation
An attacker (a DKG participant, reachable purely via public DKG messages) who has observed an honest `EncryptedMessage` from Alice to victim V (containing `msg.key = g^k` and a ciphertext) can send V a forged `EncryptedMessage` reusing Alice's `msg.key` but with a ciphertext that decrypts to non-canonical scalar bytes. V's `calculate_share` computes `key = ecdh(enc_key_V, msg.key)` — the *same* shared key that decrypts Alice's genuine message — wraps it in an `EncryptionKeyProof`, and early-returns it as publishable blame before V's batch verifier would reject the forged PoP. When V publishes the blame proof, any observer can apply `cipher(context, proof.key)` to Alice's original ciphertext and recover her secret share to V. This leaks a Shamir share of the threshold secret to all observers, degrading the threshold security of the generated key — the exact side effect the PoP was introduced to prevent.

### Likelihood Explanation
The attacker only needs to be a DKG participant (or observe authenticated DKG traffic) and submit one malformed share message — public inputs to `EncryptedMessage::read` / `calculate_share`. No collusion or key knowledge is required. The corruption of the ciphertext is trivial (flip bytes so the plaintext is non-canonical). The only constraint is that the victim must publish the blame proof, which is the protocol's intended behavior on `InvalidShare`.

### Recommendation
Verify the PoP synchronously (or run a targeted batch verification for that participant) inside `Encryption::decrypt` before computing/returning `EncryptionKeyProof`, returning `blame: None` when the PoP is invalid — mirroring how `DecryptionError::InvalidSignature` yields no publishable key. Alternatively, gate the early `from_repr` error path in `calculate_share` on having already verified the batch, or return `None` blame on that path since a corrupted ciphertext that fails PoP cannot be meaningfully attributed with a key reveal.

### Proof of Concept
1. Run PedPoP DKG with participants including Alice (honest), Victim V (honest), and Eve (malicious).
2. Eve observes Alice's `EncryptedMessage` `M_A = { key: g^{k_A}, pop, msg: C_A }` addressed to V.
3. Eve constructs `M_E = { key: g^{k_A}, pop: <arbitrary/invalid>, msg: C_A XOR pad }` so decryption under `ecdh(enc_V, g^{k_A})` yields non-canonical scalar bytes.
4. Eve sends `M_E` as her share to V.
5. V's `calculate_share` queues `M_E.pop` into the batch, computes `proof.key = enc_V * g^{k_A}`, decrypts to garbage, hits the `from_repr` error, and returns `InvalidShare { participant: Eve, blame: Some(proof) }` — without ever calling `batch.verify_with_vartime_blame()` (which would have failed `M_E`'s PoP).
6. V publishes `proof`. Anyone computes `cipher(context, proof.key)` and applies it to `C_A` from `M_A`, recovering Alice's secret share to V.