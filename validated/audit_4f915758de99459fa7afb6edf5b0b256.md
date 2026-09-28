### Title
Blame path reveals ECDH key before deferred PoP verification, leaking a copied share's plaintext - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The TensorFlow bug class is "validation deferred for performance; a later stage assumes the earlier stage succeeded." In PedPoP, `EncryptedMessage`'s proof-of-possession (PoP) is deliberately deferred to a `BatchVerifier`, yet `KeyMachine::calculate_share` acts on the decrypted plaintext — and emits an `EncryptionKeyProof` that publicly reveals the ECDH shared key — before that batch verification ever runs. This is precisely the side effect the per-message PoP was introduced to prevent (crypto/dkg/pedpop/src/encryption.rs:84-91): an attacker reusing an observed public key can coerce the victim into publishing the ECDH key that decrypts another, legitimately-encrypted share.

### Finding Description
`Encryption::decrypt` queues the Schnorr PoP into a `BatchVerifier` under `BatchId::Decryption(l)` and performs the ECDH + ChaCha20 decryption immediately, returning `(plaintext, EncryptionKeyProof { key: ecdh, dleq })` (crypto/dkg/pedpop/src/encryption.rs:479-500). The PoP is only checked later at `batch.verify_with_vartime_blame()` (crypto/dkg/pedpop/src/lib.rs:493).

However, inside `calculate_share`, between queuing the PoP and verifying the batch, the code does:

```rust
let share = Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0))
  .ok_or_else(|| PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) })?);
```

(crypto/dkg/pedpop/src/lib.rs:479-482). If the decrypted bytes are not a canonical scalar, `calculate_share` returns early with `blame: Some(blame)` — an `EncryptionKeyProof` containing `key = ecdh(enc_key_recipient, msg.key)` and a valid DLEq binding it to `msg.key` — without ever checking whether `msg.pop` verified. The early return at line 482 bypasses the `batch.verify_with_vartime_blame()` call at line 493, so a `Decryption(l)` PoP failure is never observed on this path.

This inverts the ordering assumed by the security argument in encryption.rs:84-91: the PoP exists so that "Eve [observing] Alice encrypt to Bob with key X, then send[ing] Bob a message also claiming to use X" cannot cause Bob's blame proof to "reveal bX, revealing Alice's message to Bob." With the PoP check deferred past the blame-emitting path, that guarantee no longer holds for the canonical-scalar failure branch.

### Impact Explanation
Concrete attack reachable from public inputs (an authenticated DKG share message the attacker causes to be processed):

1. Eve observes Alice's honest `EncryptedMessage<SecretShare>` to Bob (public channel), learning Alice's ephemeral key `X`.
2. In a later (or parallel) key-gen session with the same `context` and Bob's unchanged `enc_pub_key`, Eve sends Bob an `EncryptedMessage` with `key = X` (reused, whose discrete log Eve does not know), a garbage `pop`, and random ciphertext. `EncryptedMessage::read` accepts all of this; canonicity of points/scalars is satisfied by construction.
3. In `calculate_share`, `msg.pop` is queued (deferred), ECDH is computed with `X`, the garbage ciphertext decrypts to garbage, and `from_repr` fails — returning `InvalidShare { participant: Eve, blame: Some(proof) }` where `proof.key = ECDH(bob_enc, X)`, exactly the `bX` the PoP was designed to keep secret.
4. The processor propagates this blame proof in `ProcessorMessage::InvalidShare` (processor/src/key_gen.rs:420-425) and it is verifiable by any third party via `decrypt_with_proof`/`AdditionalBlameMachine` (processor/src/key_gen.rs:504-549), which accepts it: the DLEq verifies against `msg.key = X`, and the PoP failure on Eve's message still correctly blames Eve — but the published proof now decrypts Alice's original ciphertext, disclosing Alice's secret share value to Bob (and to any observer of the blame, e.g., on-chain verifiers).

The leak is one DKG share `f_Alice(Bob)`; combined with the share-verification data, a recovering attacker who collects `t` such leaked shares (repeating the trick against multiple senders, since each `msg.key` differs per message) can reconstruct the group secret polynomial contributions. The primitive leaked (a per-participant ECDH decryption key for a message never proven legitimate) is exactly the disclosure the protocol comments classify as "a massive side effect which could break some protocols."

Additionally, the ordering means the `blame: Some(...)` variant is emitted for a case (`Decryption` failure) that the batch path would have reported as `blame: None` — mismatched blame semantics for the same underlying fault, weakening the blame protocol's precision.

### Likelihood Explanation
Requires the attacker to be a DKG participant (or to submit share messages to one) and to observe a prior honest share message under the same context/encryption-key pair — both within the protocol's threat model (untrusted share bytes fed to `calculate_share`/`EncryptedMessage::read`). Deterministic trigger: any ciphertext that decrypts to a non-canonical scalar (random bytes suffice with overwhelming probability) forces the early return before the PoP batch check. No collusion, no trusted position, no unsafe code required.

### Recommendation
In `KeyMachine::calculate_share` (crypto/dkg/pedpop/src/lib.rs:476-499), do not emit an `EncryptionKeyProof` before the PoP batch is resolved. Concretely:

- Queue the `from_repr` canonicality failure as a deferred blame decision instead of returning early: record `pending[l] = blame` for the non-canonical case, run `batch.verify_with_vartime_blame()` first, and only attach `blame` if participant `l`'s PoP actually verified. If `BatchId::Decryption(l)` fails, report `blame: None` (self-evident fault) and never construct or return the ECDH-revealing proof.
- Equivalently, verify `msg.pop` synchronously (or gate `decrypt`'s proof construction on PoP success) so no `EncryptionKeyProof` is ever produced for a message that failed proof-of-possession — matching the invariant documented in encryption.rs:84-91.

### Proof of Concept
```rust
// Setup: honest DKG session, context C, params t/n. Alice -> Bob share msg A:
//   A = encrypt(rng, C, alice, enc_pub_bob, share_A)  // A.key = X
// Bob derives ECDH k = ecdh(bob_enc, X); cipher(C,k) decrypts A.msg to share_A.

// Eve (a participant in a key-gen with the same context, same Bob enc key):
let mut forged = EncryptedMessage::<C, SecretShare<C::F>> {
    key: A.key,                 // reuse X; Eve does NOT know dlog(X)
    pop: invalid_signature,     // any SchnorrSignature that fails verify
    msg: Zeroizing::new(SecretShare(random_bytes)), // garbage ciphertext
};
// EncryptedMessage::read accepts this (all encodings canonical).

// Bob calls machine.calculate_share(rng, {eve: forged}):
//  1. decrypt() queues PoP under BatchId::Decryption(Eve)  -> never verified
//  2. cipher decrypts garbage -> C::F::from_repr fails
//  3. early return: InvalidShare { participant: Eve,
//        blame: Some(EncryptionKeyProof { key: ecdh(bob_enc, X), dleq: valid }) }
//  -> batch.verify_with_vartime_blame() at line 493 is never reached

// Bob publishes blame (ProcessorMessage::InvalidShare{ blame: Some(proof.serialize()) }).
// Anyone (and Bob himself) now uses proof.key to run:
//   cipher::<C>(context, &proof.key).apply_keystream(A.msg.as_mut())
// recovering share_A = f_Alice(Bob) in the clear — the disclosure the PoP
// was added to prevent (encryption.rs:84-91).
```