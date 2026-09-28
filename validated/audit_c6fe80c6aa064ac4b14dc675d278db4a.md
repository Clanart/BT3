### Title
Panic via out-of-set `Participant` index in PedPoP blame/decryption path allows remote DoS - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CVE-2022-37770 (a segfault reachable with a crafted file), `Decryption::decrypt_with_proof` in the PedPoP DKG encryption layer performs unchecked `HashMap` indexing on `self.enc_keys[&decryptor]`, where `decryptor` is an attacker-influenced `Participant` value passed into `BlameMachine::blame` / `AdditionalBlameMachine::blame`. Since `Participant::new` accepts any nonzero `u16`, an accusation naming a `recipient` who was never registered (any index outside `1 ..= n`, or the accuser's own index `i`, which is deliberately never inserted into `enc_keys`) causes an index panic, crashing the process instead of returning a `PedPoPError`.

### Finding Description
In `Decryption::register`, encryption keys are only stored for participants that completed round 1 registration; the local participant's own index is never inserted [1](#0-0) . `decrypt_with_proof` then indexes `self.enc_keys[&decryptor]` directly when an `EncryptionKeyProof` is supplied [2](#0-1) . This is reached through the public blame API, `BlameMachine::blame` / `AdditionalBlameMachine::blame`, which accept attacker-supplied `sender`/`recipient`/`proof` arguments and forward them unvalidated into `blame_internal` [3](#0-2) . The same unchecked indexing pattern exists at `self.commitments[&sender]` and `self.commitments[&l]` in `blame_internal` and `calculate_share` [4](#0-3) .

The required malicious message bytes (`EncryptedMessage::read`, `EncryptionKeyProof::read`) deserialize without issue — all `read` functions return `io::Result` [5](#0-4)  — and the panic only occurs when the deserialized proof path is exercised against an unregistered `decryptor`. Notably, `AdditionalBlameMachine` is explicitly documented as usable by non-participants to evaluate blame over arbitrary accusations [6](#0-5) , so its `blame` entry point must be robust to arbitrary `sender`/`recipient` pairs; it is not.

### Impact Explanation
Any party able to submit a blame accusation (or trigger blame evaluation) naming a `recipient` outside the registered set — including the accuser's own index — forces a panic in the DKG blame path. In a validator/processor deployment, this is an unwind across the crypto boundary that aborts the DKG session or crashes the handler thread, yielding the same availability impact class as the CVE (DoS via crafted input). Severity: Medium — no key material is leaked, but a single crafted blame invocation reliably crashes honest nodes evaluating the accusation.

### Likelihood Explanation
Blame handling exists precisely to arbitrate disputes initiated by (potentially malicious) participants, so attacker-controlled `sender`/`recipient` values are the designed input. A faulty participant being accused will naturally attempt to deflect blame by issuing accusations with out-of-range indices; unlike `verify_r1`/`calculate_share`, which call `validate_map` to bound inputs [7](#0-6) , the blame path performs no membership check before indexing. Likelihood is therefore moderate: it requires reaching the blame phase, but needs only one malformed accusation.

### Recommendation
Replace direct indexing with fallible lookups in the blame path:
- In `Decryption::decrypt_with_proof`, use `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` instead of `self.enc_keys[&decryptor]` [8](#0-7) .
- In `blame_internal`/`calculate_share`, use `self.commitments.get(&sender)` / `.get(&l)` and return blame against whichever party supplied the invalid reference, or a dedicated error, rather than panicking [4](#0-3) .
- Consider changing `blame`/`AdditionalBlameMachine::blame` to return `Result<_, PedPoPError>` so unresolvable accusations (e.g., involving `recipient == params.i()` or indices `> n`) are reported, not panicked on.

### Proof of Concept
```rust
// After a DKG completes (or via AdditionalBlameMachine::new with valid
// commitment messages for participants 1..=n), evaluate a blame
// accusation where `recipient` is any Participant not in the registered
// enc_keys set, e.g. Participant::new(n + 1) or the evaluator's own i.
let malicious_recipient = Participant::new(n + 1).unwrap();

// A sender's EncryptedMessage<SecretShare> with a valid pop (trivially
// produced by an honest-looking round-2 sender), plus a Some(proof)
// EncryptionKeyProof< C >::read(...) whose DLEq verifies.
blame_machine.blame(
    sender,             // any registered sender
    malicious_recipient,// NOT in enc_keys -> HashMap index panic
    msg,                // EncryptedMessage::read(bytes) — valid pop
    Some(proof),        // EncryptionKeyProof::read(bytes) — valid DLEq
);
// decrypt_with_proof reaches `self.enc_keys[&decryptor]` ->
// thread panics on missing key -> DoS.
```
The panic occurs at `crypto/dkg/pedpop/src/encryption.rs` (`self.enc_keys[&decryptor]` inside `decrypt_with_proof`) before any validity conclusion is reached, so the accusation crashes the evaluator instead of being attributed to either party.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-177)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-361)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-392)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L56-83)
```rust
// Validate a map of values to have the expected included participants
fn validate_map<T, C: Ciphersuite>(
  map: &HashMap<Participant, T>,
  included: &[Participant],
  ours: Participant,
) -> Result<(), PedPoPError<C>> {
  if (map.len() + 1) != included.len() {
    Err(PedPoPError::IncorrectAmountOfParticipants {
      expected: included.len(),
      found: map.len() + 1,
    })?;
  }

  for included in included {
    if *included == ours {
      if map.contains_key(included) {
        Err(PedPoPError::DkgError(DkgError::DuplicatedParticipant(*included)))?;
      }
      continue;
    }

    if !map.contains_key(included) {
      Err(PedPoPError::MissingParticipant(*included))?;
    }
  }

  Ok(())
}
```

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

**File:** crypto/dkg/pedpop/src/lib.rs (L638-662)
```rust
impl<C: Ciphersuite> AdditionalBlameMachine<C> {
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
  pub fn new(
    context: [u8; 32],
    n: u16,
    mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
  ) -> Result<Self, PedPoPError<C>> {
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
  }
```
