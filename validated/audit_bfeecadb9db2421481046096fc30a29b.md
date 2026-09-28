### Title
Panic via out-of-range `Participant` index in PedPoP blame evaluation causes denial of service - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` (and the internal `Decryption::decrypt_with_proof`) index `HashMap`s with `Participant` values supplied by the accusing/accusing-evaluated party, without checking that the participant exists. An attacker who submits a blame request naming a `sender` or `recipient` that was not a DKG participant triggers a `HashMap` index panic, crashing the host process — the Serai analog of CVE-2023-47169 (improper buffer restrictions → unprivileged denial of service).

### Finding Description
`BlameMachine::blame` takes `sender: Participant` and `recipient: Participant` directly from the caller (who relays an accusation from an arbitrary party) and forwards them to `blame_internal` [1](#0-0) . Inside `blame_internal`, after `decrypt_with_proof` succeeds or is skipped, the code evaluates `self.commitments[&sender]` (line 599), which panics if `sender` was never inserted into the commitments map — i.e., any index that was not one of the `n` DKG participants [2](#0-1) .

The same unchecked indexing exists earlier in the call path: `Decryption::decrypt_with_proof` evaluates `self.enc_keys[&decryptor]` (encryption.rs line 388) before verifying the DLEq proof, so a `proof` argument combined with a non-registered `recipient` panics regardless of the message's validity [3](#0-2) .

`Participant::new` accepts any nonzero `u16`, and `Participant` itself is just a wrapper constructible from attacker-influenced indices; `AdditionalBlameMachine::blame` is explicitly documented as callable "regardless of if the caller was a member in the DKG protocol" [4](#0-3) .

### Impact Explanation
The panic aborts the thread/process evaluating the blame claim. In a validator/coordinator deployment, a single forged or malformed blame accusation sent by any unprivileged party kills the node handling it, denying service — matching the CVE class (availability loss via missing input restriction, CVSS availability-high). Additionally, a panic unwinding through `KeyMachine`/`BlameMachine` state can destroy the in-progress DKG, forcing a restart of key generation.

### Likelihood Explanation
High reachability, low attacker cost: the attacker needs only to cause the victim to evaluate a blame claim where `sender` or `recipient` is a valid `Participant` (nonzero `u16`) that is not in `1..=n`. `AdditionalBlameMachine::new` even iterates `1 ..= n` and only populates the map for those indexes, so any `sender > n` reliably panics [5](#0-4) . No valid signatures or secret knowledge are required for the `commitments[&sender]` path once a syntactically parseable message is supplied (the index panic occurs inside the share-validity check reached after decryption succeeds, and for the `enc_keys[&decryptor]` path it occurs unconditionally before proof verification).

### Recommendation
Replace `self.commitments[&sender]` and `self.enc_keys[&decryptor]` with checked lookups (`get`) that treat an unknown `sender`/`recipient` as fault of the accusing party or return a dedicated error, rather than panicking. Validate that both `sender` and `recipient` are within `params` participant range at the top of `blame`/`blame_internal`.

### Proof of Concept
```rust
// After an honest DKG with n participants, produce a BlameMachine or
// AdditionalBlameMachine for context `ctx` and params (t, n).
// An attacker submits a blame accusation naming a non-existent sender:
let fake_sender = Participant::new(n + 1).unwrap(); // valid Participant, not in 1..=n
let real_recipient = Participant::new(1).unwrap();
let msg = EncryptedMessage::<C, SecretShare<C::F>>::read(&mut bytes, params).unwrap();
// Path 1: proof = Some(...) -> Decryption::decrypt_with_proof indexes
// self.enc_keys[&decryptor]; if recipient is also forged -> panic immediately.
// Path 2: decrypt_with_proof succeeds or errs with InvalidSignature/InvalidProof;
// when it reaches the share-validity check:
//   self.commitments[&fake_sender]  -> HashMap index panic -> process crash.
machine.blame(fake_sender, real_recipient, msg, proof);
``` [6](#0-5)

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L575-609)
```rust
  fn blame_internal(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };

    let Some(share) = Option::<C::F>::from(C::F::from_repr(share_bytes.0)) else {
      // If this isn't a valid scalar, the sender is faulty
      return sender;
    };

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

    // The share was canonical and valid
    recipient
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L623-632)
```rust
  pub fn blame(
    self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> (AdditionalBlameMachine<C>, Participant) {
    let faulty = self.blame_internal(sender, recipient, msg, proof);
    (AdditionalBlameMachine(self), faulty)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L639-648)
```rust
  /// Create an AdditionalBlameMachine capable of evaluating Blame regardless of if the caller was
  /// a member in the DKG protocol.
  ///
  /// Takes in the parameters for the DKG protocol and all of the participant's commitment
  /// messages.
  ///
  /// This constructor assumes the full validity of the commitment messages. They must be fully
  /// authenticated as having come from the supposed party and verified as valid. Usage of invalid
  /// commitments is considered undefined behavior, and may cause everything from inaccurate blame
  /// to panics.
```

**File:** crypto/dkg/pedpop/src/lib.rs (L654-661)
```rust
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
```

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
