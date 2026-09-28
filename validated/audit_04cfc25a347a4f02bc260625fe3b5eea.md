### Title
Blame resolution uses accusation-supplied `Participant` indexes as map keys into the registered-participant set, panicking on indexes that were never registered - (`crypto/dkg/pedpop/src/encryption.rs`)

### Summary
The reference bug uses an identifier that is valid on one chain (`destlToken` on Chain A) to look up state on another chain (`lTokenToUnderlying` / `findCrossChainCollateral` on Chain B), where it does not exist, breaking the protocol flow. The same bug class exists in `dkg-pedpop`'s blame-handling path: `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept `sender` and `recipient` `Participant` indexes supplied by an accusation (untrusted input), then use them directly as keys into `HashMap`s that were only populated for the actual DKG participants `1..=n`. An accusation naming an out-of-set index causes a panic (indexing a `HashMap` with `[]`), aborting blame resolution instead of cleanly identifying the faulty party.

### Finding Description
`Decryption::register` is only ever called for the DKG's real participants: `SecretShareMachine::verify_r1` registers each `l` in `self.params.all_participant_indexes()` [1](#0-0) , and `AdditionalBlameMachine::new` registers only `1..=n` [2](#0-1) . `enc_keys` therefore contains exactly the participating indexes.

When processing a blame claim, `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` whenever a proof is attached [3](#0-2) . `decryptor` is the `recipient` index passed to `BlameMachine::blame` / `AdditionalBlameMachine::blame` [4](#0-3) , and `blame_internal` passes it through unchecked [5](#0-4) . `Participant` only guarantees non-zero; nothing restricts `recipient`/`sender` to `1..=n` or to registered keys.

Likewise, if decryption succeeds, `blame_internal` indexes `self.commitments[&sender]` to build the share-verification statements [6](#0-5) , where `commitments` is again only keyed by real participants [7](#0-6) .

This mirrors the reference root cause exactly: an identifier meaningful in the accusation's context (any syntactically valid `Participant`) is used to index a map that only exists for a different context (the registered participant set), so the lookup fails — here as a panic rather than a `found == false` — and the resolution flow cannot proceed.

### Impact Explanation
Any party able to submit a blame/accusation message (which the DKG's threat model treats as untrusted, caller-authenticated input — the library explicitly states blame inputs "must have been authenticated as actually having come from the sender in question" [8](#0-7) ) can crash the node evaluating blame by naming a `recipient` or `sender` index outside `1..=n`. Instead of attributing fault to the malicious accuser or accused, evaluation panics. In a coordinator that resolves `InvalidDkgShare` accusations via `AdditionalBlameMachine`/`VerifyBlame` [9](#0-8) , this is a denial of service on the dispute-resolution path — the same category of harm as the reference issue's liquidation flow failing silently, here manifesting as a crash that can stall the key-rotation protocol or take down evaluators.

### Likelihood Explanation
Triggering requires only that an accusation carry an out-of-set participant index plus a well-formed `EncryptedMessage` (the accuser can generate a valid PoP and DLEq proof against a registered recipient's encryption key, or simply name an unregistered `decryptor`, which panics on the `enc_keys` index before any proof check [10](#0-9) ). No cryptographic break or collusion is needed. Reachability in a given deployment depends on whether the caller bounds-checks indexes before invoking `blame`, which the library does not enforce — it even documents that misuse "may cause everything from inaccurate blame to panics" only for `AdditionalBlameMachine::new` inputs, leaving `blame`'s index handling as an implicit assumption [11](#0-10) .

### Recommendation
In `blame_internal`, validate `sender` and `recipient` against the known participant set before any map indexing: return early (e.g., blame the party supplying the invalid index, or a dedicated error) if `!self.commitments.contains_key(&sender)` or `!self.encryption.enc_keys.contains_key(&recipient)` — the analog of the reference fix to resolve `payload.srcToken`/`srcEid` against the local chain's registry instead of blindly using a foreign-context identifier. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `get`/`ok_or`-style lookups that return `DecryptionError`/`PedPoPError` rather than panicking [12](#0-11) [6](#0-5) .

### Proof of Concept
```rust
// crypto/dkg/pedpop: accusation naming a participant index outside the DKG set.
// n = 4 DKG; an accuser calls:
//   AdditionalBlameMachine::new(context, 4, commitment_msgs) // registers 1..=4
//   .blame(sender: Participant(2), recipient: Participant(7), msg, Some(proof))
// decrypt_with_proof evaluates `self.enc_keys[&decryptor]` for decryptor = 7,
// which was never registered -> panic: "no entry found for key"
// (encryption.rs, `self.enc_keys[&decryptor]` inside the DLEq verify arguments).
//
// Alternatively, blame(sender: Participant(9), recipient: Participant(2), msg, proof)
// with a validly constructed EncryptedMessage + EncryptionKeyProof (the accuser knows
// the ephemeral scalar and can produce the DLEq) reaches
// `self.commitments[&sender]` -> panic for unregistered sender 9 (lib.rs).
```

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L313-315)
```rust
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);
```

**File:** crypto/dkg/pedpop/src/lib.rs (L511-520)
```rust
    let mut verification_shares = HashMap::new();
    for i in self.params.all_participant_indexes() {
      verification_shares.insert(
        i,
        if i == self.params.i() {
          C::generator() * self.secret.deref()
        } else {
          multiexp_vartime(&exponential::<C>(i, &stripes))
        },
      );
```

**File:** crypto/dkg/pedpop/src/lib.rs (L575-588)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L596-601)
```rust
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
```

**File:** crypto/dkg/pedpop/src/lib.rs (L615-617)
```rust
  /// The message should be a copy of the encrypted secret share from the accused sender to the
  /// accusing recipient. This message must have been authenticated as actually having come from
  /// the sender in question.
```

**File:** crypto/dkg/pedpop/src/lib.rs (L646-648)
```rust
  /// authenticated as having come from the supposed party and verified as valid. Usage of invalid
  /// commitments is considered undefined behavior, and may cause everything from inaccurate blame
  /// to panics.
```

**File:** crypto/dkg/pedpop/src/lib.rs (L656-660)
```rust
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L674-682)
```rust
  pub fn blame(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    self.0.blame_internal(sender, recipient, msg, proof)
  }
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

**File:** coordinator/src/tributary/handle.rs (L486-505)
```rust
        let Some(share) = DkgShare::get(self.txn, genesis, accuser.into(), faulty.into()) else {
          self.fatal_slash(
            signed.signer.to_bytes(),
            "InvalidDkgShare had a non-existent faulty participant",
          );
          return;
        };
        self
          .processors
          .send(
            self.spec.set().network,
            key_gen::CoordinatorMessage::VerifyBlame {
              id: KeyGenId { session: self.spec.set().session, attempt },
              accuser,
              accused: faulty,
              share,
              blame,
            },
          )
          .await;
```
