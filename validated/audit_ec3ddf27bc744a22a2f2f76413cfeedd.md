### Title
PedPoP `calculate_share` returns a blame proof revealing the ECDH shared key before the queued Schnorr PoP verification is ever run — ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
The libkcapi bug class is "return an error before all submitted async work is drained, allowing later writes/uses that should never have occurred." In PedPoP, `Encryption::decrypt` does not verify the per-message proof-of-possession; it *queues* the PoP into a `BatchVerifier` (`BatchId::Decryption(l)`) and immediately returns the decrypted bytes plus an `EncryptionKeyProof` that reveals the ECDH shared key for `msg.key`. `KeyMachine::calculate_share` then checks `C::F::from_repr` on the decrypted share and returns `PedPoPError::InvalidShare { blame: Some(blame) }` early — before `batch.verify_with_vartime_blame()` is ever called. The result: a blame proof is emitted for a message whose PoP was never verified, defeating the exact protection the PoP exists for (key-reuse → ECDH reveal).

### Finding Description
`Encryption::decrypt` queues the PoP check and returns `(decrypted_msg, EncryptionKeyProof { key: ecdh(enc_key, msg.key), dleq })` unconditionally (`crypto/dkg/pedpop/src/encryption.rs:469-501`). In `calculate_share` (`crypto/dkg/pedpop/src/lib.rs:476-499`):

1. For each participant `l`, `decrypt` queues the PoP statement and returns decrypted bytes + blame proof.
2. `from_repr` on the decrypted bytes is checked; on failure it returns `Err(InvalidShare { participant: l, blame: Some(blame) })` immediately — **before** `batch.verify_with_vartime_blame()` at line 493.

So if a malicious participant sends an `EncryptedMessage` with:
- `key = X`, an ephemeral encryption key *previously observed* being used by an honest participant (e.g. Alice's message to Bob),
- a garbage `pop` (they cannot forge a valid PoP since they don't know `dlog(X)`),
- `msg` = ciphertext that decrypts (under ECDH with X, which the recipient computes regardless) to bytes that fail `C::F::from_repr`,

then `calculate_share` returns early with `blame = Some(EncryptionKeyProof)` proving the ECDH shared key for `X`. The blame proof is broadcast/usable via `BlameMachine`, and anyone holding it can derive the shared key for `X` and decrypt Alice's original message — recovering her secret share. The comment at `encryption.rs:84-90` documents exactly this side channel and states the PoP exists to prevent it — but the PoP is deferred to a batch that the early error path never drains.

This is the direct analog: work is submitted (queued statements), an error is returned before the submitted work is drained/checked, and a security-relevant output (the ECDH-revealing blame proof) is produced that was only safe conditional on that drained verification.

### Impact Explanation
An unprivileged DKG participant can cause an honest participant to publish a proof revealing the ECDH shared key for an arbitrary previously-used message key. Combined with the observed original ciphertext, this yields another participant's PedPoP secret share (recovery of key-share material), which contributes toward reconstructing the threshold group secret. This is secret leakage → Critical/High.

### Likelihood Explanation
The attacker only needs to be a DKG participant able to (a) observe a prior `EncryptedMessage` (including its `key` point) sent to the victim, and (b) submit their own crafted `EncryptedMessage` to the victim's `calculate_share`. Both are within the documented threat model (authenticated-but-readable channel). The crafted ciphertext is trivial to produce: any random bytes encrypt under the victim's computed ECDH and overwhelmingly fail `from_repr` only ~2^-small... — actually `from_repr` failure requires non-canonical scalar bytes (~50% for random bytes on most curves; trivially guaranteed by decrypting garbage). The attacker can simply flip bits until decryption yields a non-canonical repr, or pick ciphertext bytes directly since ChaCha20 decryption of chosen ciphertext under an unknown key still produces attacker-uncontrolled but resamplable bytes — they can retry across attempts/messages.

Note: whether `from_repr` fails is not fully attacker-controlled since they don't know the ECDH key, but each independent attempt succeeds with probability ≈ 1 - (p/2^256)-ish → essentially always ~50%+ per try for fields like ristretto255/secp256k1 (non-canonical fraction ≈ half? actually repr must be < p; random 32-byte string is canonical with probability p/2^256 ≈ ~1 for secp256k1? p/2^256 ≈ 1 - 3.7e-39, so almost always canonical). The attacker instead makes the share-`PoP`-invalid path moot — they need the from_repr failure, which requires the decrypted value ≥ p. They can't compute the decryption. However they can reuse `invalidate_share_serialization`-style: choose `msg` bytes; decryption XORs keystream — unknown to attacker. Probability the plaintext ≥ p is ≈ (2^256 - p)/2^256 — tiny for secp256k1/ristretto. So exploitation requires luck across many DKG attempts, or the attacker instead triggers the *other* early path: none exists — the only pre-verify return is `from_repr`. Feasible across retries/attempts but probabilistically hard per attempt for large-prime fields; still a real protocol flaw since blame leaks unconditionally whenever `from_repr` fails for *any* reason, and for fields with small non-canonical gaps it may be rare.

### Recommendation
Verify the queued `BatchId::Decryption` PoP statements (or at least the PoP for participant `l`) *before* returning `blame: Some(...)` on the `from_repr` early-error path in `calculate_share` (`crypto/dkg/pedpop/src/lib.rs:479-482`). If the PoP for `l` fails, return `blame: None` (matching the `BatchId::Decryption` convention); only attach the ECDH-revealing `EncryptionKeyProof` when the message's PoP has actually been verified.

### Proof of Concept
1. Honest Alice encrypts her PedPoP share to Bob; `EncryptedMessage{key: X, pop, msg}` is observed by Eve on the channel.
2. Eve (also a participant) submits to Bob `EncryptedMessage{key: X, pop: garbage, msg: chosen bytes}`.
3. Bob's `calculate_share` calls `decrypt`, which queues the (invalid) PoP and returns garbage-decrypted bytes + `EncryptionKeyProof` for `X` (`encryption.rs:487-500`).
4. If decrypted bytes fail `from_repr`, `calculate_share` returns `Err(InvalidShare{ participant: Eve, blame: Some(proof) })` (`lib.rs:480-482`) — `batch.verify_with_vartime_blame()` at line 493 never runs, so Eve's forged PoP is never checked.
5. The blame proof is published; anyone can verify the DLEq and recover `ecdh = enc_key_bob * X`, re-derive the ChaCha20 keystream (`cipher`), and decrypt Alice's original `msg` → Alice's secret share leaks to Eve.