### Title
PedPoP share-decryption emits an ECDH-revealing blame proof before the proof-of-possession is verified, enabling key-share recovery - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to JUSDBank checking `_isAccountSafeAfterBorrow` on `borrow` but not on `withdraw` — letting users combine operations to bypass a cap — `KeyMachine::calculate_share` gates validity on a batched proof-of-possession (PoP) check, yet on a malformed secret share it returns early and emits an `EncryptionKeyProof` that reveals the ECDH shared key *before* the batch verifier ever evaluates the PoP. The PoP exists specifically to prevent an attacker who copies someone else's per-message key from coercing a victim into publishing the ECDH key that decrypts the victim's legitimate message (encryption.rs lines 83-90; `spec/cryptography/Distributed Key Generation.md` lines 28-35). The early-return path bypasses that protection.

### Finding Description
In `Encryption::decrypt` (crypto/dkg/pedpop/src/encryption.rs:469-501), the per-message PoP signature is only *queued* into a `BatchVerifier` (`msg.pop.batch_verify`, line 479); the ECDH key is then computed and the message decrypted, and the function returns `(msg, EncryptionKeyProof { key: ecdh(..), dleq })` — the shared key is materialized and handed back unconditionally.

In `KeyMachine::calculate_share` (crypto/dkg/pedpop/src/lib.rs:476-499):

```rust
let (mut share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
let share =
  Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
    PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
  })?);   // <-- early return: batch (containing the PoP check) is never verified
```

If the decrypted bytes are not a canonical scalar — which is the case for arbitrary garbage ciphertext, since a random 32-byte string almost never encodes a valid scalar — the `?` returns `InvalidShare { blame: Some(blame) }` *before* `batch.verify_with_vartime_blame()` at line 493 runs. The PoP statement sitting in the batch (which would fail, since the attacker never knew the per-message key's discrete log) is never evaluated. Even when `from_repr` happens to succeed, a subsequent batch failure mapped at lines 493-498 yields `blame: Some(...)` whenever the blame search happens to return the `BatchId::Share` half of the split rather than `BatchId::Decryption`.

The correct ordering exists in the sibling path `Decryption::decrypt_with_proof` (encryption.rs:373-397), which verifies `msg.pop.verify(...)` *first* and returns `DecryptionError::InvalidSignature` — attributing fault to the sender — before any proof/key material is consumed. The PoP check is the "`_isAccountSafeAfterBorrow`" of this protocol; the `calculate_share` error path is the "`withdraw`" that skips it.

### Impact Explanation
A blame proof is designed to be published (coordinator `VerifyBlame` consumes `EncryptionKeyProof`; `BlameMachine::blame`/`AdditionalBlameMachine::blame` re-decrypt with `proof.key`). `proof.key` is exactly `victim_enc_priv * msg.key`, and the attached DLEq binds it to `[G, msg.key] → [victim_enc_pub, proof.key]` — verifiable against the victim's registered encryption key regardless of the forged PoP.

Attack: a participant Eve observes Alice's `EncryptedMessage` to Bob — `key = X`, `pop`, `ciphertext` are all serialized in cleartext (only `msg` is encrypted). Eve sends Bob her own secret-share `EncryptedMessage` reusing `msg.key = X` with a garbage PoP and garbage ciphertext. Bob's `calculate_share` decrypts with `bX = bob_enc_priv * X`, hits the non-canonical-scalar early return, and emits `blame = Some(EncryptionKeyProof { key: bX, .. })` — which Bob publishes as a blame proof. Anyone can now derive the `cipher(context, bX)` keystream and decrypt Alice's *real* message to Bob, recovering Alice's secret share `s_{A→B}`.

By replaying the copied per-message key from Alice's message to *each* victim, Eve collects `t` shares of Alice's polynomial and fully reconstructs Alice's contributed secret, breaking the DKG's confidentiality for that dealer (and compounding across all honest dealers yields group-key-share recovery). This is precisely the side-effect the PoP was added to prevent ("When they do, they'd reveal bX, revealing Alice's message to Bob" — encryption.rs:86-88), reintroduced by the unchecked error path.

### Likelihood Explanation
- Reachable by any unprivileged DKG participant: `EncryptedMessage::read`/`calculate_share` operate on attacker-supplied bytes; Eve only needs to observe cleartext `msg.key` fields relayed for other participants (messages pass through the coordinator/network, per `processor/src/key_gen.rs` `VerifyBlame` handling).
- Deterministic: Eve controls the ciphertext, so she can ensure the decrypted bytes fail `C::F::from_repr` (or rely on random failure probability ~1); the early `?` guarantees the batch never runs.
- Cost: one malformed share message per victim; failure modes all still emit the blame proof containing the ECDH key.

### Recommendation
Verify the per-message PoP *before* decrypting or materializing any blame proof in `Encryption::decrypt` / `KeyMachine::calculate_share` — i.e., always apply the "full safety check" on every path that produces an `EncryptionKeyProof`, mirroring `decrypt_with_proof`'s ordering. Concretely: make `decrypt` evaluate (or synchronously verify) `msg.pop` prior to `ecdh`, and return `blame: None`/`InvalidSignature` when it fails, so no ECDH key is ever emitted for a message whose per-message key the sender did not prove possession of. Alternatively, in `calculate_share`, run the queued PoP statements through the batch verifier before any early-return path can wrap `blame` into `PedPoPError::InvalidShare`.

### Proof of Concept
1. Run PedPoP with participants `{Eve=1, Alice=2, Bob=3}`, `t=2, n=3`. Alice's `generate_secret_shares` emits `Enc_{A→B} = EncryptedMessage { key: X, pop: σ_X, msg: ct_{A→B} }` where `ct` encrypts `s_{A→B}` under `cipher(context, ecdh(bob_priv, X))`.
2. Eve, also a participant, constructs `Enc_{E→B} = EncryptedMessage { key: X, pop: garbage_sig, msg: 32-byte garbage }` and delivers it to Bob as her share.
3. Bob calls `calculate_share`:
   - `decrypt` queues `garbage_sig` into `batch` (never verified), computes `bX = bob_priv * X`, "decrypts" garbage, and returns `EncryptionKeyProof { key: bX, dleq }`.
   - `C::F::from_repr(garbage)` → `None` → early `?` returns `InvalidShare { participant: Eve, blame: Some(proof_with_bX) }`.
4. Bob publishes the blame. Anyone computes `cipher(context, bX)` (the DLEq confirms `bX` is correct for `[G, X]·bob_enc_pub`) and XORs it against `ct_{A→B}`, recovering `s_{A→B}` — a share Eve was never authorized to see.
5. Repeating per-recipient for Alice's shares to `t` distinct victims yields `t` evaluations of Alice's polynomial → Lagrange reconstruction of Alice's secret coefficients → dealer key-share recovery, despite Eve never possessing `x` for `X`.

Root-cause lines: crypto/dkg/pedpop/src/lib.rs:476-499 (early `?` before batch verification), crypto/dkg/pedpop/src/encryption.rs:469-501 (ECDH key returned before PoP enforced), contrasted with crypto/dkg/pedpop/src/encryption.rs:373-379 (correct PoP-first ordering in the blame path).