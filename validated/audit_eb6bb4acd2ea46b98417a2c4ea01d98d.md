### Title
Missing participant-index validation in PedPoP blame path causes panic (fatal abort) instead of fault attribution - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The Unbound bug class here is: a rarely exercised path (threshold-triggered cleanup) calls a function not present in an allow-list, so the process fatally exits instead of performing the expected handling. The Serai analog lives in the PedPoP blame-verification path: `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept arbitrary `sender`/`recipient` `Participant` identifiers and index `HashMap`s with them (`self.enc_keys[&decryptor]`, `self.commitments[&sender]`) without checking they are within `1 ..= n`. A blame accusation naming a participant outside the DKG set triggers a `HashMap` index panic — an abrupt abort of the embedding application — rather than returning the faulty party.

### Finding Description
`blame` delegates to `blame_internal`, which calls `Decryption::decrypt_with_proof`. That function, when a `proof` is supplied, evaluates the DLEq statement over `self.enc_keys[&decryptor]`: [1](#0-0) 

`enc_keys` is populated only for participants registered via `register`, i.e., indexes `1 ..= n` (`AdditionalBlameMachine::new` registers exactly `1 ..= n`): [2](#0-1) 

Neither `blame`, `blame_internal`, nor `decrypt_with_proof` validates `sender`/`recipient` against the participant set — unlike the well-guarded paths (`validate_map` in `calculate_share`/`verify_r1`, `ThresholdParams::new` bounds checks). If `recipient` is a `Participant` with index `> n` (or `sender` is), the `HashMap` indexing operator panics. The same unchecked indexing occurs again in `blame_internal` at `self.commitments[&sender]` when verifying the decrypted share: [3](#0-2) 

Additionally, `Decryption::register` `assert!`s that a participant is not re-registered (encryption.rs:356-359); `AdditionalBlameMachine::new` itself documents that invalid inputs "may cause ... panics" (lib.rs:646-648), yet `blame` performs no validation of the accusation's participant fields at all.

### Impact Explanation
Availability impact analogous to the advisory: when the rare blame-verification path is reached, a missing guard (the equivalent of the absent allow-list entry) causes a fatal panic terminating the host process instead of attributing fault. In Serai's deployment shape, blame verification is invoked over an accusation whose fields (`sender`/`accuser`, `recipient`/`accused`, the encrypted share bytes readable via `EncryptedMessage::read`, and the optional `EncryptionKeyProof::read` proof) are supplied by the accusing party — an unprivileged participant in the protocol. The panic aborts the processor/coordinator evaluating the blame, denying service precisely when the protocol is attempting to recover from a fault, and prevents the guilty party from being identified/slashed.

### Likelihood Explanation
Triggering requires only that an accusation be filed where `recipient` (or `sender`) is a `Participant` value not in `1 ..= n` — `Participant` is a bare `u16` wrapper and no bound check exists on this path. The attacker must get a blame evaluation to run (a fault must be alleged), which is a normal protocol operation, not an exceptional precondition. The claim needs a valid PoP-signed message only for the `enc_keys[&decryptor]` panic via the `proof: Some` branch; the `commitments[&sender]` panic is reached whenever `decrypt_with_proof` returns `Ok` (i.e., with a legitimately formed message from an in-set sender and valid proof, while naming an out-of-set `recipient`... wait — the `decryptor` panic precedes it). More simply: any `decrypt_with_proof` call with `proof: Some(_)` and `decryptor` out of set panics before signature-dependent logic. Likelihood is moderate: it requires the blame phase to run, which requires an accusation to exist — matching the Medium, AC:H character of the original advisory.

### Recommendation
In `blame_internal` (and/or `Decryption::decrypt_with_proof`), check that `sender` and `recipient`/`decryptor` are members of the participant set (`self.commitments.contains_key(&sender)`, `self.enc_keys.contains_key(&decryptor)`), and treat an out-of-set accuser field as the accuser being faulty — or return a dedicated error — rather than indexing the maps. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `.get()` + error handling. Also consider making `Decryption::register`'s re-registration `assert!` an error return for defense in depth.

### Proof of Concept
```rust
// t-of-n PedPoP completed; obtain an AdditionalBlameMachine over the
// commitment messages (as done for post-hoc blame adjudication).
let blame_machine = AdditionalBlameMachine::<Ristretto>::new(CONTEXT, n, commitment_msgs).unwrap();

// A real encrypted share message from participant ONE to TWO (valid PoP),
// plus a Some(EncryptionKeyProof) so decrypt_with_proof proceeds to the
// enc_keys lookup.
let msg: EncryptedMessage<Ristretto, SecretShare<_>> = /* share 1->2, via EncryptedMessage::read */;
let proof = Some(encryption_key_proof);

// Accusation names a recipient/decryptor NOT in the DKG set.
let out_of_set = Participant::new(n + 1).unwrap();

// decrypt_with_proof reaches `self.enc_keys[&decryptor]` (encryption.rs:388)
// which panics: "no entry found for key" — aborting the application instead
// of returning sender/recipient fault attribution.
let _faulty = blame_machine.blame(ONE, out_of_set, msg, proof);
```

Analogously, `blame(sender = Participant::new(n + 1), recipient, msg_with_valid_pop_and_proof)` reaches `self.commitments[&sender]` (lib.rs:~599) and panics on the missing key. Both are reachable purely from attacker-chosen participant identifiers plus untrusted bytes parsed by `EncryptedMessage::read`/`EncryptionKeyProof::read`, matching the bug class of a threshold-triggered path invoking an unhandled/missing-guard operation that fatally aborts the embedding application.

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

**File:** crypto/dkg/pedpop/src/lib.rs (L595-605)
```rust
    // If this isn't a valid share, the sender is faulty
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L656-661)
```rust
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
```
