### Title
Missing participant-index validation causes panic (node crash) in PedPoP blame evaluation - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The analog of CVE-2016-3137 (missing check for a required component → NULL dereference / crash) exists in PedPoP's blame path. `BlameMachine::blame_internal` indexes `self.commitments[&sender]` without checking that `sender` was actually a member of the DKG. `Participant::new` only rejects zero, so any caller-controlled `sender`/`recipient` index `> n` produces a `HashMap` lookup on a missing key and panics, aborting the process instead of returning a blame verdict.

### Finding Description
`AdditionalBlameMachine::new` builds the `commitments` map strictly for `Participant` 1..=n: [1](#0-0) 

`blame` / `AdditionalBlameMachine::blame` then call `blame_internal(sender, recipient, msg, proof)` with attacker-influenced `Participant` values. The verification of the accused's share does an unchecked map index: [2](#0-1) 

There is no check that `sender` or `recipient` is within `1..=n`. `Participant` permits any non-zero `u16`, so `sender = Participant::new(n + 1)` passes all type-level validation and hits `self.commitments[&sender]` → panic (`HashMap` index on absent key). This is exactly the CVE's shape: a struct member that is assumed present because the "descriptor" (participant index) nominally looks valid is dereferenced without an existence check.

The same pattern exists at `KeyMachine::calculate_share` (`self.commitments[&l]`, line 490), but there `validate_map` at lines 468-472 constrains `l` to `all_participant_indexes()`, so it is defended. The blame path has no equivalent guard — `blame_internal` never validates `sender`/`recipient` against the known participant set, and `AdditionalBlameMachine`'s documented purpose is to evaluate blame for observers who are not DKG members, meaning arbitrary `(sender, recipient)` pairs are part of its input surface.

Reachability: blame adjudication is driven by externally supplied accusations — `sender` and `recipient` indexes and the `EncryptedMessage`/`EncryptionKeyProof` bytes all come from the accusing/accused parties' messages (e.g., the `VerifyBlame { accuser, accused, share, blame }` flow). An unprivileged participant (or any party able to submit a blame object) can name an out-of-range index.

### Impact Explanation
A single malformed blame accusation referencing `Participant` index `> n` panics the thread executing `blame_internal`. In a node/processor context this is an abort of the blame-handling path (and, depending on executor, the process) — a remote denial of service identical in effect to the kernel NULL dereference: the code trusts that an index drawn from untrusted input corresponds to an entry that was populated only for in-range members. No keys or funds are leaked; impact is availability only → Medium.

### Likelihood Explanation
Triggering requires submitting a blame evaluation request with `sender` (or reaching the multiexp path with an invalid `recipient`) outside `1..=n`. `Participant::new(n+1)` is trivially constructible, and the input is attacker-controlled in the blame workflow, so likelihood is moderate — it requires the node to evaluate blame, which is a normal (if infrequent) code path.

### Recommendation
In `blame_internal` (or the `blame` wrappers), validate `sender` and `recipient` against `self.commitments` before indexing — return a defined verdict (or error) rather than indexing the map. E.g., treat an out-of-range `sender` as faulty (`return sender`) or return a `Result`/`Option`. Mirror the `validate_map`-style bounds check used in `calculate_share`.

### Proof of Concept
```rust
// crypto/dkg/pedpop
let n: u16 = 3;
let machine = AdditionalBlameMachine::<Secp256k1>::new(context, n, commitment_msgs).unwrap();
// commitment_msgs legitimately covers participants 1..=3

// Attacker submits a blame claim naming a non-existent participant
let fake_sender = Participant::new(4).unwrap(); // valid Participant, not in the DKG
machine.blame(fake_sender, Participant::new(1).unwrap(), msg, None);
// -> blame_internal -> multiexp_vartime(&share_verification_statements(... &self.commitments[&sender] ...))
// -> panic: HashMap index on missing key
```

The panic occurs at `crypto/dkg/pedpop/src/lib.rs:599` (`&self.commitments[&sender]`) because `commitments` only contains keys `1..=n` while `sender` is unconstrained beyond being non-zero.

### Citations

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
