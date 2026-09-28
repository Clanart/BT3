### Title
Panic on unregistered participant index in PedPoP blame decryption — reachable crash from attacker-controlled DKG messages - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The iccDEV bug is an out-of-bounds read while iterating an array whose extent is controlled by file bytes. Serai is memory-safe, so the direct analog is an unchecked index/panic on attacker-influenced input rather than a heap read. `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` directly; `enc_keys` only contains participants that previously called `register`, so any `decryptor` value not in the map causes a panic (`HashMap` index returns `&V` and panics on missing keys). [1](#0-0) 

### Finding Description
`decrypt_with_proof` is used in PedPoP's blame path: an accuser reveals an `EncryptionKeyProof` so a third party can decrypt a specific message. Before verifying the DLEq proof it computes `&[self.enc_keys[&decryptor], *proof.key]` (encryption.rs:388). Registration into `enc_keys` happens only via `Decryption::register` (encryption.rs:351-362), which inserts strictly the keys of participants that registered. If the blame/decryption flow is invoked with a `decryptor` `Participant` whose encryption key was never registered (e.g., the accused supplies/omits their `EncryptionKeyMessage`, or an accuser targets a non-registered index), the indexing panics inside library code rather than returning `DecryptionError`. The same unchecked-index pattern exists in `Encryption::encrypt` at `self.decryption.enc_keys[&participant]` (encryption.rs:466) and in `BindingFactor`'s `self.0[&i]`/`self.0[&l]` accessors (crypto/frost/src/nonce.rs:176,183). Note: `BindingFactor::nonces` indexing `commitments.nonces[n]` (nonce.rs:204-206) is *not* vulnerable because `Commitments::read` bounds the vector to `generators.len()` (nonce.rs:133-138), so lengths always match.

### Impact Explanation
An unprivileged DKG participant who can trigger the blame path (by submitting a blame/`EncryptionKeyProof` naming a `decryptor` that is absent from `enc_keys`) causes a panic in the victim's PedPoP handling code. In a validator/processor context this aborts the key-generation session and crashes the thread/task — a denial of service analogous in impact to the iccDEV crash (CVSS A:H). Because `std::panic` unwinds through `io`/`Result` boundaries, callers expecting `DecryptionError` cannot handle it.

### Likelihood Explanation
Medium likelihood: it requires the blame path to be exercised with a `decryptor` that skipped (or was never processed for) encryption-key registration — a state a malicious or crashed participant can induce by not broadcasting a valid `EncryptionKeyMessage` while still being referenced in an accusation. It is bounded to session DoS; no secret leakage follows, since the panic occurs before the ChaCha20 keystream is applied (encryption.rs:392).

### Recommendation
Replace `self.enc_keys[&decryptor]` with `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` (or a dedicated `UnknownParticipant` variant), and apply the same to `self.decryption.enc_keys[&participant]` in `Encryption::encrypt`. Audit the `self.0[&i]`/`self.0[&l]` accesses in `BindingFactor` similarly, returning `FrostError::MissingParticipant` where the participant may be absent.

### Proof of Concept
1. In a PedPoP session, have participant P never submit a valid `EncryptionKeyMessage`, so `Decryption::register` is never called for P and `enc_keys` lacks `P`.
2. Trigger the blame flow so `decrypt_with_proof(from, decryptor = P, msg, Some(proof))` is invoked with a PoP-valid `EncryptedMessage` and any `EncryptionKeyProof` (the `dleq.verify` call is evaluated with `&self.enc_keys[&decryptor]` as an argument, so the index is evaluated before `verify` runs).
3. `self.enc_keys[&P]` panics with "key not found" instead of returning `Err(DecryptionError::InvalidProof)`.

Uncertainty: I verified the panic site and that `enc_keys` is populated only via `register`, but I did not fully trace `pedpop/src/lib.rs` to confirm the exact public message that carries the attacker-chosen `decryptor` into `decrypt_with_proof`. If the caller always restricts `decryptor` to a locally-known set, reachability is reduced and this becomes a robustness bug rather than a remotely-triggered panic.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-390)
```rust
    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;
```
