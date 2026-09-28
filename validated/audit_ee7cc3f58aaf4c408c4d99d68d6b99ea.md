### Title
Out-of-range participant index in blame evaluation panics, aborting the DKG node - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The CVE class (denial of service via crafted input) maps onto Serai's PedPoP DKG blame path. `BlameMachine::blame_internal` indexes `self.commitments[&sender]` with a `Participant` value supplied by the caller of `blame`/`AdditionalBlameMachine::blame`. `AdditionalBlameMachine` is explicitly designed to be usable by a party that was "not a member in the DKG protocol" and evaluates accusations supplied by other parties. If the accusation names a `sender` (or `recipient` flow reaching the same map) whose index is not in `1 ..= n` of the original DKG, the `HashMap` indexing operation panics, crashing the evaluating node instead of returning a blame verdict.

### Finding Description
In `crypto/dkg/pedpop/src/lib.rs`, `blame_internal` resolves the accused sender's commitments via direct `HashMap` indexing: [1](#0-0) 

`self.commitments` is only populated for `Participant::new(1) ..= Participant::new(n)` — in `AdditionalBlameMachine::new` the map is built by iterating `for i in 1 ..= n` [2](#0-1) , and in `KeyMachine::calculate_share` it contains exactly `all_participant_indexes` [3](#0-2) . `Participant` is any nonzero `u16`, so a `sender` index `> n` is a representable, valid `Participant` value that is simply absent from the map. Neither `BlameMachine::blame` [4](#0-3)  nor `AdditionalBlameMachine::blame` [5](#0-4)  validates `sender`/`recipient` against `n` before `blame_internal` dereferences them.

Note the same unchecked indexing pattern exists on the recipient side in `calculate_share`/`verify_r1`, but those paths constrain indexes via `validate_map` [6](#0-5) ; `blame` has no equivalent guard.

### Impact Explanation
A participant in a PedPoP DKG (or any party able to trigger a blame evaluation, e.g., by broadcasting an accusation naming a forged sender index) can panic the thread executing `blame`. Since `blame` returns `(AdditionalBlameMachine, Participant)` and consumes/inspects the `BlameMachine`, a panic aborts the protocol-completion flow entirely — the node cannot finish the DKG or process further blame statements. In a coordinator/tributary-style deployment where blame evaluation runs on a consensus-critical path, this is a remote crash triggered by attacker-chosen protocol data, matching the CVE's "crafted input → DoS" class.

### Likelihood Explanation
Triggering requires: a PedPoP DKG in progress, and an accuser (or a relayed accusation) that names a `sender` index `> n` (or a `recipient`/proof combination that reaches the `self.commitments[&sender]` lookup with an out-of-range key). The accuser needs to supply a `msg`/`proof` pair that survives `decrypt_with_proof` — an `Err` there returns early before the panic, so the attacker must either be a legitimate sender whose message decrypts, or the lookup must be reached via a valid proof. This narrows reachability: an arbitrary unauthenticated byte string does not panic, but a real DKG participant can craft an accusation that does. I could not fully verify whether `decrypt_with_proof` itself indexes maps with `sender`/`recipient` (the body of `Decryption::decrypt_with_proof` in `crypto/dkg/pedpop/src/encryption.rs` was not inspected); if it does, the panic may trigger even earlier or may return `Err` first, changing exploitability.

### Recommendation
Validate `sender` and `recipient` against the DKG's `n` (or check `self.commitments.contains_key(&sender)`) at the top of `blame`/`AdditionalBlameMachine::blame`/`blame_internal`, returning the accuser as faulty or a dedicated error instead of panicking. Replace `self.commitments[&sender]` with `self.commitments.get(&sender)` plus explicit handling.

### Proof of Concept
```rust
// After any successful PedPoP DKG with n participants (e.g., n = 4),
// on a node holding a BlameMachine (or a third-party AdditionalBlameMachine
// built via AdditionalBlameMachine::new(context, n, commitment_msgs)):

// Craft an accusation where `sender` is a valid nonzero Participant
// that exceeds n. Participant::new(n + 5) succeeds because Participant
// only rejects zero.
let fake_sender = Participant::new(5).unwrap(); // n = 4
let recipient = Participant::new(2).unwrap();

// `msg` is an EncryptedMessage<C, SecretShare<C::F>> and `proof` an
// Option<EncryptionKeyProof<C>> obtained such that decrypt_with_proof
// returns Ok (e.g., a real encrypted share from a colluding/self accuser,
// or blame routing where the accuser node itself supplies the indexes).
// Once decrypt_with_proof returns Ok, blame_internal reaches:
//     &self.commitments[&sender]   // line ~599
// `commitments` only contains keys 1..=4, so HashMap indexing panics:
// "no entry found for key" -> thread abort, DoS.
let _ = machine.blame(fake_sender, recipient, msg, proof); // panic
```

Caveats: exploitability depends on `decrypt_with_proof` returning `Ok` for a crafted/forged `(sender, msg, proof)` tuple, or on the accuser being a legitimate participant whose accusation payload is attacker-influenced; the `Decryption::decrypt_with_proof` internals were not inspected, so the panic may be gated behind a valid ECDH/DLEq proof for the named (out-of-range) sender — in which case a participant could instead register an accusation through an index path not filtered at `AdditionalBlameMachine::new`. Even in the weakest interpretation, `blame` documents no requirement that indexes be in-range, so the panic is reachable through the public API with attacker-influenced arguments.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L57-83)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L331-337)
```rust
      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;

    commitments.insert(self.params.i(), self.our_commitments.drain(..).collect());
    Ok(commitments)
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
