### Title
PedPoP blame proof reveals the ECDH shared key before the message's proof-of-possession is verified - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The `rack-mini-profiler` advisory is an information-disclosure caused by performing a security check in the wrong order. The analog in Serai is in the PedPoP DKG: `KeyMachine::calculate_share` returns an `EncryptionKeyProof` — which contains the ECDH shared key `b * msg.key` computed with the recipient's long-term encryption secret — on the "share bytes are non-canonical" error path, *before* the batch verification that validates the sender's proof-of-possession (PoP) for `msg.key` ever runs. This re-enables exactly the key-confusion attack the PoP was added to prevent.

### Finding Description
`EncryptedMessage` carries a per-message key `key` and a Schnorr PoP proving ownership of that key. The comment at `crypto/dkg/pedpop/src/encryption.rs:83-90` documents why the PoP exists: if Eve co-opts Alice's message key X, Bob's blame reveal of `bX` would decrypt Alice's message to Eve.

The enforcement is broken by ordering:

1. `Encryption::decrypt` (`crypto/dkg/pedpop/src/encryption.rs:469-501`) only *queues* the PoP via `msg.pop.batch_verify(...)` (line 479), then unconditionally computes `ecdh(&self.enc_key, msg.key)` and returns it inside `EncryptionKeyProof { key, dleq }` (lines 487-499). The shared key is materialized and handed to the caller before the PoP is verified.
2. In `KeyMachine::calculate_share` (`crypto/dkg/pedpop/src/lib.rs:476-492`), the per-sender loop calls `decrypt`, then immediately checks `C::F::from_repr(share_bytes.0)`; on failure it returns `Err(PedPoPError::InvalidShare { participant: l, blame: Some(blame) })` via `?` at lines 480-482. This early return happens *before* `batch.verify_with_vartime_blame()` at line 493, so the queued PoP for that `msg.key` is never checked.
3. The only place the batch failure maps blame to `None` (the `BatchId::Decryption` arm at line 495) is unreachable in this scenario — the function has already returned the `Some(blame)` containing the ECDH shared key.

An unprivileged DKG participant Eve therefore: observes the public `key` field `X_B` of Alice's `EncryptedMessage` addressed to Bob (it is a plaintext group element on the wire), sends Bob her own `EncryptedMessage { key: X_B, pop: <garbage>, msg: <random bytes> }`. Bob computes `b_B * X_B` — the exact shared key protecting Alice's share to Bob — decrypts to garbage, hits the `from_repr` error path (a random 32-byte string is a non-canonical scalar with high probability, e.g. ~15/16 for a ~2^252-order field), and returns `blame = Some(proof)` with `proof.key = b_B * X_B` and a valid DLEq over it. Bob's correct behavior is to publish this blame; anyone can then decrypt Alice's ciphertext to Bob.

### Impact Explanation
Disclosure of `b_B * X_B` lets Eve recover the secret share Alice sent to Bob (`cipher(context, proof.key).apply_keystream` over Alice's `msg`). Collecting shares this way across senders reconstructs a victim's FROST secret key share, i.e. key-share recovery — a loss of confidentiality for the threshold key material, matching the "sensitive information disclosure via misordered security check" class of CVE-2016-4442. Severity: Medium–High (secret share recovery by an unprivileged protocol participant, no collusion required beyond being a DKG participant able to observe routed messages' public headers).

### Likelihood Explanation
The trigger is fully attacker-controlled: Eve chooses arbitrary `msg` bytes and any valid point as `key`. The PoP is never verified on this path, so no forgery is needed. The only requirement is that the decrypted bytes fail `from_repr`, which holds with overwhelming probability for random ciphertext (and Eve can retry with different garbage each attempt). The victim Bob follows the protocol honestly and is induced to emit the blame proof.

### Recommendation
Do not return or rely on `EncryptionKeyProof` until the message's PoP has been verified. Concretely:

- In `KeyMachine::calculate_share`, verify each message's PoP *before* decryption, or restructure so the `from_repr` failure path cannot emit `blame` for a message whose `BatchId::Decryption(l)` statement was never confirmed — e.g. run `batch.verify_with_vartime_blame()` (or a per-message immediate PoP `verify`) before interpreting decrypted bytes, and only attach `Some(blame)` after the PoP passed.
- Alternatively, have `Encryption::decrypt` verify the PoP synchronously (`msg.pop.verify(...)`) and refuse to compute/return the ECDH key and `EncryptionKeyProof` when it fails, mirroring the ordering already used in `Decryption::decrypt_with_proof` (`encryption.rs:374-379`), which checks the signature before touching the proof key.

### Proof of Concept
```rust
// 3-of-3 DKG, Ciphersuite = Ristretto.
// Alice = p1, Bob = p2, Eve = p3.

// Round 1: all generate coefficients/commitments as usual.
// Round 2: Alice encrypts her share to Bob:
let alice_to_bob: EncryptedMessage<Ristretto, SecretShare<_>> =
    alice_shares[&p2].clone(); // observed on the wire; `key` field is public

// Eve extracts Alice's per-message key X_B = alice_to_bob.key and forges
// a message to Bob reusing it, with an invalid PoP and random ciphertext:
let evil = EncryptedMessage::<Ristretto, SecretShare<_>> {
    key: alice_to_bob.key,               // co-opted key (pub(crate)/via serialize)
    pop: SchnorrSignature { R: random_point, s: random_scalar }, // invalid
    msg: Zeroizing::new(SecretShare(random_32_bytes)),
};
let mut shares_to_bob = honest_shares_map;
shares_to_bob.insert(p3 /* Eve */, evil);

// Bob runs calculate_share. `decrypt` queues (but never verifies) Eve's PoP,
// computes ecdh(bob_enc_key, X_B), decrypts to garbage, and from_repr fails
// -> early return BEFORE batch.verify_with_vartime_blame():
let Err(PedPoPError::InvalidShare { participant: p3, blame: Some(proof) }) =
    bob_key_machine.calculate_share(rng, shares_to_bob) else { panic!() };

// proof.key == bob_enc_key * X_B == the ECDH key of Alice's message to Bob.
// Eve obtains it when Bob publishes the blame, then:
cipher::<Ristretto>(context, &proof.key)
    .apply_keystream(alice_to_bob_ciphertext.as_mut());
// alice_to_bob_ciphertext now yields Alice's secret share for Bob.
```
Note: `key`/`msg` fields are private; in a real deployment Eve obtains `X_B` by parsing Alice's serialized `EncryptedMessage` on the wire (it is a plaintext `GroupEncoding` preceding the PoP), which is sufficient for the attack — the PoC only needs struct access in a test harness.