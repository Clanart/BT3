### Title
Out-of-range participant index in a blame accusation panics `blame_internal`, aborting the DKG - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept an attacker-supplied `sender` and `recipient` `Participant` and index `self.commitments` with them via `HashMap` indexing (`self.commitments[&sender]`). A `Participant` value outside the DKG's `1..=n` set is never validated, so indexing the map panics, crashing/aborting any node processing the accusation. Analogous to the Nextcloud metadata DoS: a malformed, unprivileged input makes the protocol's stored state (the in-progress DKG result) permanently inaccessible.

### Finding Description
In `blame_internal`, the accused `sender` is used directly as a `HashMap` key:

```rust
// crypto/dkg/pedpop/src/lib.rs:596-604
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

`Participant::new` only enforces non-zero (`crypto/dkg` `Participant`), so `sender` can be any `u16` in `1..=65535`, including values `> n`. `self.commitments` only contains entries for `all_participant_indexes()` (`1..=n`, populated in `verify_r1`/`AdditionalBlameMachine::new`). `HashMap`'s `Index` impl panics on a missing key, so a blame accusation naming `sender = n+1` (or any non-member index) panics instead of returning an error.

The same trust issue applies to `recipient`, which is fed into `share_verification_statements`/`exponential` (`exponential` computes `i^k` for arbitrary `i` — non-member recipients don't panic but are still unvalidated input).

Note `AdditionalBlameMachine` is explicitly designed to be usable by non-participants evaluating blame (`AdditionalBlameMachine::new`, lines 649-662), so the `blame` entry point is expected to process accusations about arbitrary (sender, recipient) pairs supplied by third parties — exactly the unprivileged-input surface the bug class targets.

### Impact Explanation
Any party that can submit a blame accusation (including, per `AdditionalBlameMachine::new`'s design, a non-participant observer) can panic the process evaluating blame by naming a `sender` index that was never part of the DKG. Where blame evaluation happens inside a coordinator/validator handling DKG fault resolution, this is a remote DoS: the node crashes or the DKG attempt cannot be resolved to completion, rendering the pending threshold key inaccessible — the direct analog of "files made inaccessible by invalid metadata."

### Likelihood Explanation
Triggering requires only that a caller route attacker-controlled `sender`/`recipient` fields into `blame`. Since the API exists precisely to adjudicate third-party accusations and takes raw `Participant` values with no membership check, a single crafted accusation suffices. Reliability is deterministic (missing-key panic on `HashMap` indexing). Impact is capped at Medium: it yields crash/abort DoS, not key recovery or forgery.

### Recommendation
Validate in `blame`/`blame_internal` (and `AdditionalBlameMachine::new` call sites) that `sender` and `recipient` are members of the committed participant set before indexing: use `self.commitments.get(&sender)` and return a defined error/fault verdict (e.g., blame the accuser or reject the accusation) instead of panicking. Replace `self.commitments[&sender]` with fallible lookup.

### Proof of Concept
```rust
// Setup: complete a PedPoP DKG with params n=3, t=2, obtaining a
// BlameMachine (or construct AdditionalBlameMachine::new(context, 3, msgs)).
let blame_machine: AdditionalBlameMachine<Ristretto> = ...;

// Attacker submits an accusation naming a non-existent sender.
let sender = Participant::new(4).unwrap();   // 4 > n = 3, but non-zero so valid Participant
let recipient = Participant::new(1).unwrap();

// Build any syntactically valid EncryptedMessage<SecretShare> that decrypts
// to a canonical scalar and a valid share for `recipient` (or drive the code
// path far enough that `self.commitments[&sender]` is evaluated).
let msg: EncryptedMessage<Ristretto, SecretShare<Fq>> = ...;

// This indexes commitments[Participant(4)] which was never inserted ->
// panics on HashMap Index (missing key) instead of returning a blame verdict.
blame_machine.blame(sender, recipient, msg, None);
```

Root cause: unvalidated map indexing at `blame_internal`, crypto/dkg/pedpop/src/lib.rs:599 (`&self.commitments[&sender]`), reachable from the public `blame` API (lines 623-632, 674-682).