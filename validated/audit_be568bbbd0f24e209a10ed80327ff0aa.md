### Title
Unchecked participant-index lookup in PedPoP blame evaluation panics on out-of-range `sender`/`recipient` - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept caller-/accuser-supplied `Participant` indexes and index `self.commitments[&sender]` via `HashMap`'s `Index` impl without first checking that the index maps to a protocol participant. This is the direct analog of CVE-2024-58052's unchecked lookup result being dereferenced: the lookup can "fail" (key absent) and the resulting panic aborts the process.

### Finding Description
The `commitments` map in `BlameMachine` is populated only for the valid participant range `1..=n` — in `calculate_share` it comes from `verify_r1`'s `all_participant_indexes()` iteration, and in `AdditionalBlameMachine::new` it is explicitly built by iterating `for i in 1 ..= n`. [1](#0-0) [2](#0-1) 

`blame_internal` then performs `self.commitments[&sender]` to run `share_verification_statements` for share validity checking, with no bounds check on `sender` (nor on `recipient`, which is used only as an index into `exponential`/`Participant` arithmetic, but `sender` is the fatal index). [3](#0-2) 

The public entry points `BlameMachine::blame` and `AdditionalBlameMachine::blame` pass `sender`/`recipient` straight through to `blame_internal`. [4](#0-3) [5](#0-4) 

There is no guard equivalent to `Participant::new(...)`'s range check (`new` only rejects 0, not `> n`) and no `get`-style handling — `HashMap::index` panics on a missing key. [6](#0-5) 

Compare with the rest of the crate, which consistently validates participant sets via `validate_map` before touching keyed data; the blame path is the one place a participant index flows from an argument into a map index unchecked. [7](#0-6) 

### Impact Explanation
Any participant index `sender` (and on some paths `recipient`) that is nonzero but outside `1..=n` — e.g., `Participant::new(0xffff).unwrap()` — reaches `self.commitments[&sender]` and panics. In Rust this is a thread/process abort, the semantic equivalent of the NULL dereference in the kernel report: an unchecked lookup whose failure is not handled. In the Serai coordinator context, a panic during DKG blame evaluation kills the validator's key-generation/blame adjudication for that session — an availability failure driven by an input any party can supply (a blame accusation names arbitrary `sender`/`recipient` indexes).

### Likelihood Explanation
Reachability requires only that a party submit a blame accusation naming an out-of-range participant index. `blame` is a public API taking `sender`/`recipient` as plain arguments; the docs require the `msg` be authenticated as coming from `sender`, but nothing in code enforces `sender <= n` before indexing. The panic additionally occurs in `decrypt_with_proof` paths for a forged-index message, and in `AdditionalBlameMachine::new`'s externally-instantiable form where the accuser may not even be a protocol member. Caveat I could not fully verify without the full `encryption.rs` internals: if `decrypt_with_proof` returns `Err` before the `commitments` index for a bogus sender, the panic site moves earlier but the class (unchecked lookup on attacker-named index) is unchanged.

### Recommendation
Validate `sender` and `recipient` at the top of `blame`/`blame_internal` against the participant set (`u16::from(sender) <= params.n` or `self.commitments.contains_key(&sender)`), returning `Err(PedPoPError::MissingParticipant(..))` or a defined "invalid blame" result instead of indexing. Replace `self.commitments[&sender]` with `self.commitments.get(&sender)` and propagate `None` as an error. Apply the same guard in `AdditionalBlameMachine::blame`.

### Citations

**File:** crypto/dkg/src/lib.rs (L29-35)
```rust
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```

**File:** crypto/dkg/src/lib.rs (L195-197)
```rust
  pub fn all_participant_indexes(&self) -> impl Iterator<Item = Participant> {
    AllParticipantIndexes { i: 1, n: self.n }
  }
```

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
