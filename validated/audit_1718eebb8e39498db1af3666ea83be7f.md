### Title
Panic (instead of error) on unregistered participant index in PedPoP blame evaluation causes process abort - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2026-45819 describes a library calling `process.exit()` on invalid input instead of returning an error, turning bad input into denial of service. The Serai analog is unchecked `HashMap` indexing in the PedPoP blame path: `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` and `BlameMachine::blame_internal` indexes `self.commitments[&sender]`. Both maps only contain entries for participants `1..=n` registered during the DKG, yet `sender`/`recipient` in `blame()` / `AdditionalBlameMachine::blame()` are attacker-controlled `Participant` values embedded in a blame accusation. Supplying an index that was never registered panics the thread instead of returning an error — the Rust equivalent of the reported exit-instead-of-throw bug class.

### Finding Description
- `Decryption::decrypt_with_proof` verifies the message PoP, verifies the optional DLEq proof, then dereferences `self.enc_keys[&decryptor]` with `HashMap::index`, which panics on a missing key. `enc_keys` is populated only by `Decryption::register` for real DKG participants. [1](#0-0) 
- `BlameMachine::blame_internal` takes `sender`/`recipient` straight from the accusation and forwards them to `decrypt_with_proof`, then indexes `self.commitments[&sender]` — again a panicking index over a map containing only real participants. [2](#0-1) 
- `AdditionalBlameMachine::new` registers only `Participant::new(1..=n)`; an accusation naming `Participant(n+1)` (a valid `Participant`, just not part of this DKG) is never filtered before the indexing lookups. [3](#0-2) 
- The public entry points `BlameMachine::blame` and `AdditionalBlameMachine::blame` accept the accusation (`sender`, `recipient`, `msg`, `proof`) verbatim — these are the untrusted bytes an accusing party supplies. [4](#0-3) 

### Impact Explanation
Any party able to submit a blame accusation (a DKG participant, or anyone invoking the standalone `AdditionalBlameMachine` evaluator) can crash the evaluating node/thread by naming a `sender` or `recipient` index outside `1..=n`. Like the referenced CVE, invalid input causes immediate termination rather than a `Result` error, aborting whatever consensus/signing pipeline invoked blame evaluation — a medium-severity availability loss reachable with only attacker-controlled message fields.

### Likelihood Explanation
Triggering requires only that the accuser produce an `EncryptedMessage` whose Schnorr PoP verifies (the accuser can create one with their own key) and name an unregistered `recipient` index (e.g., `n+1`); execution then reaches `enc_keys[&decryptor]` and panics. No secret knowledge or collusion is needed. Alternatively, naming an unregistered `sender` reaches `commitments[&sender]` once decryption succeeds.

### Recommendation
Replace panicking `HashMap` indexing with lookups that return `PedPoPError`/`DecryptionError`: use `enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` in `decrypt_with_proof` and `commitments.get(&sender).ok_or(...)?` in `blame_internal`, and reject `sender`/`recipient` outside `all_participant_indexes()` at the `blame()` entry points.

### Proof of Concept
```rust
// In a DKG with params n, all participants registered 1..=n.
// Attacker submits a blame accusation:
let evil_recipient = Participant::new(n + 1).unwrap(); // valid Participant, never registered
// msg = any EncryptedMessage whose Schnorr PoP verifies for `sender` (attacker's own)
// proof = Some(valid EncryptionKeyProof for msg)
blame_machine.blame(sender, evil_recipient, msg, proof);
// decrypt_with_proof -> self.enc_keys[&evil_recipient] -> panic! (process abort)
// instead of returning Err(DecryptionError::*)
```

Note: `AdditionalBlameMachine::new` documents that invalid commitment inputs "may cause ... panics" — the panic on an unregistered *accusation index* in `blame()` is the reachable, undocumented variant; whether downstream callers pre-validate accusation indices could not be fully confirmed within the in-scope crates.

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

**File:** crypto/dkg/pedpop/src/lib.rs (L582-600)
```rust
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
