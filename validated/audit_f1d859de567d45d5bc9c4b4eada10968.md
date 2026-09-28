### Title
Untrusted `sender`/`commitments` in `AdditionalBlameMachine`/`blame_internal` cause panic and mis-attributed blame instead of a contained error - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The reported bug class is a failure in a call to an untrusted party that is *not* contained by the surrounding error handling, letting an attacker force the whole operation to abort. The Serai analog lives in PedPoP's blame evaluation: `AdditionalBlameMachine` is explicitly designed to let any party (even a non-participant) evaluate blame over attacker-supplied `sender`/`recipient`/`msg` arguments, yet `blame_internal` performs an unchecked `HashMap` index on `self.commitments[&sender]` and `AdditionalBlameMachine::new` never validates that each registered `Commitments` vector has length `t`. The first defect is a panic (abort) on out-of-set `sender`; the second feeds a wrong-length polynomial-commitment vector into the share-verification statements, producing incorrect blame attribution instead of a clean error.

### Finding Description
`calculate_share` on the participant path is careful: `validate_map` restricts share senders to `all_participant_indexes` [1](#0-0) , and `verify_r1` rejects any `Commitments` whose length is not `t` [2](#0-1) . Neither check exists on the `AdditionalBlameMachine` path. `AdditionalBlameMachine::new` inserts `encryption.register(i, msg).commitments` directly, with no length check [3](#0-2) , and `blame_internal` indexes `self.commitments[&sender]` unconditionally [4](#0-3) . `AdditionalBlameMachine::blame` accepts an arbitrary `sender: Participant` from the caller [5](#0-4) .

Two concrete consequences:

1. **Panic instead of contained error**: if `sender` is not a key in `commitments` (e.g., a `Participant` index the map doesn't contain — including an index `> n` that `Participant::new` still accepts, or any index when `new` was called with fewer than `n` commitment messages, since missing ones only error per-index for `1..=n`), `self.commitments[&sender]` panics. The "catch" that is supposed to attribute fault (returning `sender` or `recipient`) is never reached — exactly the shape of the reference bug where `try/catch` cannot catch a call that fails before entering the target.

2. **False blame via short/empty commitment vectors**: a commitment message registered through `AdditionalBlameMachine::new` can carry `commitments.len() < t` (including 0). `share_verification_statements` then builds `exponential` over whatever length is present [6](#0-5) ; for an empty vector the statement reduces to `share·G ≟ 0`, which is never identity for a non-zero canonical share, so `blame_internal` returns `sender` as faulty [7](#0-6)  even when the accused sender's actual share was valid — blame lands on the wrong party, or an honest participant is framed, depending on which malformed commitments were registered.

### Impact Explanation
PedPoP's blame machinery exists precisely to contain faults by attributing them. Because `AdditionalBlameMachine` is documented as usable by non-participants ("capable of evaluating Blame regardless of if the caller was a member" [8](#0-7) ), any observer handling authenticated-but-untrusted blame traffic can (a) crash the blame-evaluation path by passing an unregistered `sender` — an abort the library's error model cannot express — or (b) register malformed `Commitments` so that `blame` returns a `Participant` verdict computed from invalid inputs, causing honest participants to be marked faulty or protocol completion to be sabotaged. In a deployment where blame drives slashing/exclusion, this converts a malformed-message fault into a node crash or wrongful exclusion — the same "uncontainable callback failure forces abort" class as the Teller finding. Severity: Medium (DoS / fault-attribution integrity; no secret leakage).

### Likelihood Explanation
Triggering requires only supplying a blame query (sender, recipient, message, optional proof) — public inputs an unprivileged party to the protocol can produce. The panic path needs a `sender` absent from `commitments`; the mis-attribution path needs a `Commitments` message whose vector length differs from `t`, which `Commitments::read` happily deserializes since it reads exactly `params.t()` entries from the byte stream but `register`/`new` never re-verify the semantic length against the accused's real polynomial degree. Both are reachable without any honest-party cooperation beyond the blame API being invoked.

### Recommendation
- In `AdditionalBlameMachine::new`, enforce `msg.commitments.len() == t` (or store the expected `t` and check in `blame`), mirroring the `verify_r1` check.
- In `blame_internal`, replace `self.commitments[&sender]` (and any other map indexing on attacker-controlled `Participant` values) with `get(...)` returning a defined blame/error result instead of panicking — e.g., treat an unknown `sender` as the faulty party or return an explicit error variant.
- Have `blame`/`AdditionalBlameMachine::blame` return `Result<Participant, PedPoPError>` so malformed inputs surface as catchable errors rather than aborts.

### Proof of Concept
```rust
// 1) Panic path: sender not present in commitments map.
let mut machine = AdditionalBlameMachine::<C>::new(context, n, commitment_msgs).unwrap();
// Any Participant not in 1..=n (or whose msg was missing) panics here:
machine.blame(Participant::new(n + 1).unwrap(), recipient, msg, proof); // index panic at
// self.commitments[&sender]

// 2) Mis-attribution path: register a Commitments whose Vec has len < t.
// Construct EncryptionKeyMessage<C, Commitments<C>> with commitments = [] (or any
// length != t). AdditionalBlameMachine::new accepts it. Then for a canonical non-zero
// share, share_verification_statements reduces to [share * G]; is_identity() is false,
// so blame_internal returns `sender` — blaming a participant for a "fault" that is
// actually the malformed commitment set.
```

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L317-319)
```rust
      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L430-448)
```rust
fn share_verification_statements<C: Ciphersuite>(
  target: Participant,
  commitments: &[C::G],
  mut share: Zeroizing<C::F>,
) -> Vec<(C::F, C::G)> {
  // This can be insecurely linearized from n * t to just n using the below sums for a given
  // stripe. Doing so uses naive addition which is subject to malleability. The only way to
  // ensure that malleability isn't present is to use this n * t algorithm, which runs
  // per sender and not as an aggregate of all senders, which also enables blame
  let mut values = exponential::<C>(target, commitments);

  // Perform the share multiplication outside of the multiexp to minimize stack copying
  // While the multiexp BatchVerifier does zeroize its flattened multiexp, and itself, it still
  // converts whatever we give to an iterator and then builds a Vec internally, welcoming copies
  let neg_share_pub = C::generator() * -*share;
  share.zeroize();
  values.push((C::F::ONE, neg_share_pub));

  values
```

**File:** crypto/dkg/pedpop/src/lib.rs (L468-472)
```rust
    validate_map(
      &shares,
      &self.params.all_participant_indexes().collect::<Vec<_>>(),
      self.params.i(),
    )?;
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

**File:** crypto/dkg/pedpop/src/lib.rs (L639-641)
```rust
  /// Create an AdditionalBlameMachine capable of evaluating Blame regardless of if the caller was
  /// a member in the DKG protocol.
  ///
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
