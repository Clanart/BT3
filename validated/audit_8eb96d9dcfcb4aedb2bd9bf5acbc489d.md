### Title
Unvalidated `sender`/`recipient` indexes in PedPoP blame evaluation cause a panic (remote DoS) — (File: crypto/dkg/pedpop/src/lib.rs, crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CVE-2018-18065 (a crafted packet causing a NULL-pointer crash), `BlameMachine::blame` / `AdditionalBlameMachine::blame` perform unchecked `HashMap` indexing with attacker-influenced `Participant` values. An accusation naming a `recipient` (or `sender`) outside `1..=n` causes a panic inside `HashMap` indexing, crashing the executing thread and aborting the DKG/blame protocol.

### Finding Description
`blame` accepts `sender: Participant` and `recipient: Participant` with no bound check against the DKG's `n` (`Participant` only enforces non-zero). `blame_internal` forwards them to `Decryption::decrypt_with_proof`, which indexes `self.enc_keys[&decryptor]` [1](#0-0) . `enc_keys` only contains the participants registered via `register` — either the real DKG participants in `verify_r1` or `1..=n` in `AdditionalBlameMachine::new` [2](#0-1) . Indexing a `HashMap` with `[]` panics on a missing key, so any `recipient > n` (or any unregistered index) is a panic.

If `decrypt_with_proof` succeeds (the accuser supplies a well-formed `EncryptedMessage` with a valid PoP and a valid `EncryptionKeyProof`, both of which a malicious participant can construct since they know their own encryption key), `blame_internal` then evaluates `self.commitments[&sender]` [3](#0-2) , which panics identically for `sender > n`.

The blame API is explicitly documented as consuming an accusation: "The message should be a copy of the encrypted secret share from the accused sender to the accusing recipient" [4](#0-3) , meaning `sender` and `recipient` are values parsed from a peer's accusation message and are attacker-controlled. Nothing in `blame`, `blame_internal`, or `decrypt_with_proof` validates them.

### Impact Explanation
A panic in Rust aborts the protocol evaluation and, in the coordinator/processor context running blame adjudication, crashes the task or process handling it. An unprivileged DKG participant (or anyone able to trigger a blame evaluation with crafted indexes, e.g., an outside accuser using `AdditionalBlameMachine`) can unilaterally deny service to the node evaluating the accusation, repeatedly if desired. This mirrors the CVE's authenticated, single-packet crash primitive.

### Likelihood Explanation
Triggering requires only submitting a blame accusation whose `sender`/`recipient` fields exceed `n`, plus a trivially constructible `EncryptedMessage`/`EncryptionKeyProof` for paths that need them (`participant > n` on `recipient` panics before any proof content is even checked beyond the PoP). No collusion, no validator privileges, and no leaked secrets are needed — only the ability to get a node to evaluate a blame statement, which is a designed public input path.

### Recommendation
Validate `sender` and `recipient` at the top of `blame_internal` (and in `AdditionalBlameMachine::blame`) against the registered participant set — e.g., return a deterministic `Participant` result or a new `PedPoPError` variant for out-of-range indexes instead of indexing with `[]`. Use `.get()` on `enc_keys`/`commitments` and map `None` to an error/`recipient` fault rather than panicking.

### Proof of Concept
```rust
// AdditionalBlameMachine over a 3-of-3 DKG, then evaluate a crafted accusation:
let machine = AdditionalBlameMachine::<Secp256k1>::new(context, 3, commitment_msgs).unwrap();
// Accusation claiming "recipient" = participant 0xFFFF, with a validly-PoP'd EncryptedMessage
// and Some(proof). decrypt_with_proof reaches `self.enc_keys[&decryptor]` and panics:
machine.blame(sender_p1, Participant::new(0xFFFF).unwrap(), crafted_msg, Some(proof));
// thread panics: "no entry found for key" — DoS.
```
Uncertainty note: whether the deployment layer sanitizes `sender`/`recipient` before calling `blame` is outside the in-scope crates; within the library itself, the panic is unconditional and reachable via the documented public API.

### Citations

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

**File:** crypto/dkg/pedpop/src/lib.rs (L596-605)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L611-632)
```rust
  /// Given an accusation of fault, determine the faulty party (either the sender, who sent an
  /// invalid secret share, or the receiver, who claimed a valid secret share was invalid). No
  /// matter which, prevent completion of the machine, forcing an abort of the protocol.
  ///
  /// The message should be a copy of the encrypted secret share from the accused sender to the
  /// accusing recipient. This message must have been authenticated as actually having come from
  /// the sender in question.
  ///
  /// In order to enable detecting multiple faults, an `AdditionalBlameMachine` is returned, which
  /// can be used to determine further blame. These machines will process the same blame statements
  /// multiple times, always identifying blame. It is the caller's job to ensure they're unique in
  /// order to prevent multiple instances of blame over a single incident.
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

**File:** crypto/dkg/pedpop/src/lib.rs (L654-660)
```rust
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
```
