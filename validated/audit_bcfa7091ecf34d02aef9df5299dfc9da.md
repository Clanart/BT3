### Title
Attacker-controlled participant index causes panic via unchecked `HashMap` indexing during PedPoP blame decryption - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
The QEMU floppy NULL-deref class maps onto Serai as a reachable panic: dereferencing state that was never initialized for the supplied input. `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` with an indexing operator that panics when the key is absent. The `decryptor` participant index is carried in the blame/accusation message, which is attacker-controlled input, while `enc_keys` only contains entries for participants who successfully completed `register`. An accusation naming an unregistered `decryptor` (e.g., a participant who never submitted an `EncryptionKeyMessage`, or an index a malicious accused supplies in a blame session before registration completed) causes the `HashMap` index to panic, crashing the validator process — a remote denial of service reachable with public messages.

### Finding Description
`register` only populates `enc_keys` for participants who actually submit an `EncryptionKeyMessage`, and asserts against double registration [1](#0-0) . During a blame proof, `decrypt_with_proof` verifies the accused's DLEq proof using `self.enc_keys[&decryptor]` as the expected public key [2](#0-1) . `HashMap`'s `Index` impl panics on a missing key; there is no `get`/`ok_or` handling and no validation that `decryptor` is in `params`'s participant range or was registered. The analogous pattern also exists in `Encryption::encrypt` at `self.decryption.enc_keys[&participant]` [3](#0-2) .

The panic path is reached purely by feeding crafted DKG messages (`EncryptedMessage::read`, `EncryptionKeyProof::read`, and the accusation/decryptor participant index) — all unprivileged, public inputs a malicious or colluding-free single participant can supply.

### Impact Explanation
A panic in the DKG/blame path aborts the signing/keygen process, and in an unwinding-unsafe context (the crates are used across FFI/`no_std`-adjacent boundaries in Serai's substrate runtime) aborts the node. Even where caught, the DKG session is destroyed, denying availability of the threshold signing service — matching the CVE's "privileged user crashes the process → denial of service, highest threat is availability" profile. Note: this is a panic (availability only), not secret leakage.

### Likelihood Explanation
Any DKG participant can trigger a blame session, and the `decryptor` index embedded in accusation/blame flow is serialized attacker input. Reaching an unregistered `decryptor` is possible whenever an accusation is processed for a participant index whose `EncryptionKeyMessage` was never registered — e.g., a participant that aborts after key distribution but before registration, or a malformed accusation naming an index outside the registered set. No key material, collusion, or validator privileges beyond ordinary DKG participation are needed.

### Recommendation
Replace `self.enc_keys[&decryptor]` with `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` (and likewise `enc_keys[&participant]` in `encrypt`), and validate `from`/`decryptor` against `params.all_participant_indexes()` before indexing.

### Proof of Concept
```rust
// In a PedPoP session with params where participant P never registered an
// EncryptionKeyMessage (e.g., P aborted mid-protocol), submit a blame/accusation
// naming decryptor = P. The accused's node executes:

proof.dleq.verify(
  &mut encryption_key_transcript(self.context),
  &[C::generator(), msg.key],
  &[self.enc_keys[&decryptor], *proof.key], // panics: key not found
)
```
The `HashMap` index operator panics on the missing `decryptor` entry before the DLEq proof is even evaluated, crashing the participant process.

Caveat: I verified the panic site but did not fully trace the accusation-handling callers in `crypto/dkg/pedpop/src/lib.rs` that supply `from`/`decryptor`; if the enclosing code already guarantees `decryptor` was registered (e.g., accusations only accepted from registered participants), the reachability narrows to edge cases where registration is incomplete. The fix is still warranted as defense-in-depth on an untrusted-input indexing operation.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-362)
```rust
  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    assert!(
      !self.enc_keys.contains_key(&participant),
      "Re-registering encryption key for a participant"
    );
    self.enc_keys.insert(participant, msg.enc_key);
    msg.msg
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-391)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L460-467)
```rust
  pub(crate) fn encrypt<R: RngCore + CryptoRng, E: Encryptable>(
    &self,
    rng: &mut R,
    participant: Participant,
    msg: Zeroizing<E>,
  ) -> EncryptedMessage<C, E> {
    encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)
  }
```
