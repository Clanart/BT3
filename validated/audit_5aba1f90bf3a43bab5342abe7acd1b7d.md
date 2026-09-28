Let me check the FROST signing path for the binding factor and what an unprivileged cosigner can extract.### Title
PedPoP `calculate_share` attaches the ECDH blame proof before verifying the per-message PoP, enabling forced disclosure of another participant's encrypted secret share - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
In `KeyMachine::calculate_share`, the recipient decrypts every incoming `EncryptedMessage`, generates an `EncryptionKeyProof` (which publicly reveals the ECDH shared key `b·msg.key`), and — if the decrypted bytes fail `C::F::from_repr` — immediately returns `PedPoPError::InvalidShare { blame: Some(blame) }` *before* the batched proof-of-possession (`msg.pop`) is verified. A malicious DKG participant can copy the per-message key `X` from an honest sender's encrypted share to the victim, send garbage ciphertext bound to `X` without a valid PoP, and induce the victim to publish the shared key `b·X`. That published key is exactly the key that decrypts the honest sender's real secret-share ciphertext, which is publicly visible in the `DkgShares` tributary transaction. The PoP mechanism was explicitly designed to prevent this attack, but the early `from_repr` return bypasses it.

### Finding Description
`crypto/dkg/pedpop/src/encryption.rs` documents the threat: without the per-message `pop` Schnorr signature, "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X... Bob would then use this to create a blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob" (lines 84–90). The `pop` is supposed to block this by proving the sender knows `dlog(msg.key)`.

However, in `KeyMachine::calculate_share` (`crypto/dkg/pedpop/src/lib.rs:476–499`):

```rust
let (mut share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
let share =
  Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
    PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
  })?);
```

`Encryption::decrypt` (`encryption.rs:479–499`) only *queues* `msg.pop.batch_verify(...)` into the `BatchVerifier`; the signature is not verified until `batch.verify_with_vartime_blame()` at line 493. The `from_repr` check at line 480 returns early, attaching `Some(blame)` — the `EncryptionKeyProof { key: ecdh(enc_key, msg.key), dleq }` — even when the PoP is invalid or the message's `key` was plagiarized from another sender's ciphertext.

The error propagates to `processor/src/key_gen.rs`, producing `ProcessorMessage::InvalidShare { blame: Some(...) }`, which is published as a `Transaction::DkgShares`/blame report on the tributary (`coordinator/src/tributary/transaction.rs`, `handle.rs` `VerifyBlame`). Adjudication via `decrypt_with_proof` does correctly detect the invalid PoP and blame the sender (`InvalidSignature → sender`), but by then the ECDH shared key is already public — the disclosure is irreversible.

Attack steps for malicious participant Eve targeting the share Alice→Bob:
1. Observe `EncryptedMessage { key: X, .. }` in the public shares data from Alice to Bob.
2. Send Bob an `EncryptedMessage` with `key = X`, arbitrary 32-byte ciphertext, and any `pop` value (it will fail verification, but that is never reached).
3. Bob's `cipher(context, b·X)` decrypts garbage; `from_repr` fails with probability ≈ 1 − 2^(−3) for Ristretto/Ed25519 scalars (~7/8 per attempt; retryable with new random ciphertext bytes if it happens to decode canonically — note the share-verification path at lines 487–491 would also queue blame, though on that path a PoP failure correctly maps to `BatchId::Decryption` → `blame: None`, so the non-canonical route is the reliable trigger).
4. Bob publishes `EncryptionKeyProof { key = b·X, dleq }`.
5. Anyone recomputes `cipher(context, b·X)` and XORs it against Alice's original ciphertext (public in the `DkgShares` transaction), recovering Alice's secret share `f_A(Bob)` in the clear.

### Impact Explanation
Public, on-chain disclosure of an honest participant's secret-share plaintext — a direct analog of CVE-2021-21168's "obtain potentially sensitive information via crafted input caused by insufficient policy enforcement." A single leaked `f_A(Bob)` does not alone reconstruct the group key (VSS tolerates this), but it degrades the DKG's confidentiality guarantees: the share is secret material meant to exist only between sender and recipient, and an attacker who is also a DKG participant already learns `f_A(Eve)`; combining leaked shares to multiple victims erodes the polynomial's secrecy and can be amplified across attempts and across multiple malicious participants, each triggering leaks before being slashed. It also leaks data under `Zeroizing`-protected types (`SecretShare`, `EncryptionKeyProof::key`), bypassing the codebase's confidentiality model.

### Likelihood Explanation
Reachable by any unprivileged DKG participant through public protocol inputs (`EncryptedMessage` bytes supplied to `calculate_share` / published in `DkgShares`). Requirements: participate in (or observe) one DKG attempt, copy a `msg.key` field, and submit a forged share message. Success probability per forged message is ~7/8 for Ristretto (field order ~2^252 vs 2^256 encodings). Cost to the attacker is eventual self-slash, but the disclosure happens before adjudication and cannot be undone. Severity Medium: leaks secret share material (confidentiality-only impact, requiring protocol participation and per-leak slashing cost).

### Recommendation
Verify the PoP before emitting any blame material: either verify `msg.pop` inline in `Encryption::decrypt` prior to returning the `EncryptionKeyProof`, or in `calculate_share` defer attaching `blame` until `batch.verify_with_vartime_blame()` confirms the `BatchId::Decryption(l)` statements — e.g., continue the loop with a placeholder share on `from_repr` failure and only release the `EncryptionKeyProof` after the batch verifies the PoP. Alternatively, bind the published `EncryptionKeyProof` transcript to the specific message hash so a revealed key provably belongs only to a PoP-authenticated message.

### Proof of Concept
```rust
// Eve targets the EncryptedMessage Alice sent to Bob (both public on the tributary).
let alice_to_bob: EncryptedMessage<C, SecretShare<C::F>> = /* from DkgShares tx */;

// Forged message: reuse Alice's per-message key X, garbage ciphertext, any PoP.
let mut forged = alice_to_bob.clone();
forged.msg.as_mut().as_mut().fill(0xAB);          // decrypts to non-canonical ~87% of the time
forged.pop.s += C::F::ONE;                         // invalid PoP — never verified on this path

// Bob runs calculate_share({Eve: forged, ...}):
//   decrypt() -> queues PoP into batch, computes key = ecdh(b, X), returns blame proof
//   C::F::from_repr(garbage) -> None
//   -> Err(InvalidShare { participant: Eve, blame: Some(EncryptionKeyProof { key: b·X, .. }) })
// batch.verify_with_vartime_blame() is never reached; the invalid PoP is irrelevant.

// Bob's processor publishes InvalidShare{ blame: Some(proof.serialize()) } on-chain.
// Anyone now decrypts Alice's real ciphertext:
//   cipher(context, proof.key).apply_keystream(alice_to_bob.msg) == Alice's secret share f_A(Bob)
// VerifyBlame later slashes Eve (InvalidSignature -> sender), but the share is already disclosed.
```
Relevant code: `crypto/dkg/pedpop/src/lib.rs:476–499` (early `from_repr` return with `Some(blame)` before batch PoP verification), `crypto/dkg/pedpop/src/encryption.rs:78–99` (documented attack the PoP is meant to prevent), `encryption.rs:479–499` (PoP only queued, ECDH key always returned), `encryption.rs:373–397` (blame adjudication checks PoP — too late), `processor/src/key_gen.rs:467–473,504–563` (publication and `VerifyBlame` path).