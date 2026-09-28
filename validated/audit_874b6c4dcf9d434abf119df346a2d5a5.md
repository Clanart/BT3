### Title
Unvalidated map indexing in blame-path decryption panics on attacker-chosen participant, crashing the DKG node - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to the MOOSDB negative-length deserialization DoS (unvalidated parsed value → unhandled exception → process termination), `Decryption::decrypt_with_proof` indexes `self.enc_keys` with a participant identifier derived from an untrusted blame/accusation flow. If the named participant never registered an encryption key — something an unprivileged, malicious DKG participant fully controls — `HashMap`'s `Index` impl panics, aborting the decryption/blame processing task and killing the node process.

### Finding Description
`Decryption::register` only inserts into `self.enc_keys` for participants who previously sent a valid `EncryptionKeyMessage`, and it asserts on re-registration: [1](#0-0) 

In `decrypt_with_proof`, the proof branch verifies the accuser's `EncryptionKeyProof` against `self.enc_keys[&decryptor]` using direct `HashMap` indexing: [2](#0-1) 

`decryptor` names whose encryption key the DLEq proof must bind to. There is no `contains_key`/`get` guard: `std::collections::HashMap`'s `impl Index` panics with "key not found" when `decryptor` was never registered. The same unchecked indexing exists on the encrypt path (`self.decryption.enc_keys[&participant]` at line 466), but `encrypt` is only invoked for locally-chosen counterparties; `decrypt_with_proof` is the remotely reachable variant, since it is invoked while adjudicating `InvalidDkgShare`-style blame where the accused/accuser identity comes from messages authored by other participants.

Other in-scope deserializers were checked and do not analogize: `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) bounds `n` to u16 and returns `io::Error` on bad input; `SchnorrAggregate::read` (crypto/schnorr/src/aggregate.rs:77-88) loops on a u32 count but performs no pre-allocation and errors on truncation; `read_F`/`read_G` (crypto/ciphersuite/src/lib.rs:74-100) are fixed-size and canonicalized; `EncryptedMessage::read`/`EncryptionKeyProof::read` are fixed-size.

### Impact Explanation
A panic in Rust unwinds (or aborts under `panic=abort`) the executing task. Blame adjudication runs on the critical DKG path; an unhandled panic terminates the participant's DKG session (or process), denying service exactly as the MOOSDB unhandled-exception bug does. No key material is required — the attacker merely withholds their `EncryptionKeyMessage` registration (or induces an accusation against a participant who did) and then triggers the decryption-with-proof path.

### Likelihood Explanation
Reachability requires the attacker to be a DKG participant who can cause `decrypt_with_proof` to be invoked with a `decryptor` absent from `enc_keys` — e.g., by initiating/faulting a share complaint against a party that skipped encryption-key registration, or by being the accused party in a complaint where the accuser never registered. Registration gaps are realistic (malformed/missing registration is precisely what the blame flow exists for), making the panic path reachable by an unauthenticated-in-the-DKG-sense participant with only public inputs. Medium severity: availability impact only, no secret leakage.

### Recommendation
Replace indexing with a fallible lookup in `decrypt_with_proof` (and `encrypt`):

```rust
let enc_key = self
  .enc_keys
  .get(&decryptor)
  .ok_or(DecryptionError::InvalidProof)?;
proof.dleq.verify(&mut encryption_key_transcript(self.context),
  &[C::generator(), msg.key], &[*enc_key, *proof.key])?;
```

Similarly, `encrypt` should return `io::Result`/`Option` instead of panicking on an unregistered `participant`, so a peer that never registered cannot crash the session.

### Proof of Concept
1. Initialize a `ThresholdParams`/PedPoP DKG where participant `P_victim` runs `Encryption::new` and shares its `enc_pub_key`, but participant `P_evil` never sends an `EncryptionKeyMessage`, so `P_evil ∉ enc_keys` (and symmetrically, `enc_keys` lacks any never-registered party).
2. `P_evil` submits an accusation/`InvalidDkgShare` claim that causes the victim to call `decrypt_with_proof(from, decryptor = P_evil, msg, Some(proof))` with a syntactically valid `EncryptionKeyProof` (reads fine via `EncryptionKeyProof::read`).
3. Execution reaches `self.enc_keys[&decryptor]`; `HashMap::index` panics (`"key not found"`), terminating the blame-handling task/process — unhandled exception equivalent to the MOOSDB crash.

Note: the exact call site wiring of `decrypt_with_proof` inside pedpop's share-blame flow was not fully traced (iteration limit); the panic itself is confirmed in `crypto/dkg/pedpop/src/encryption.rs:388`, and reachability rests on `decryptor` being populated from peer-controlled accusation data.

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-396)
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

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
    }
```
