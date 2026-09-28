### Title
Out-of-bounds participant index causes panic in PedPoP blame evaluation - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
CVE-2016-4952 is an out-of-bounds array access reachable via attacker-controlled ring-setup parameters, causing denial of service. The analog in Serai is unchecked indexing into the `commitments` `HashMap` in `BlameMachine::blame_internal` / `AdditionalBlameMachine::blame`: the `sender` argument is an arbitrary `Participant` (any nonzero `u16`), and `self.commitments[&sender]` panics if that participant was never registered — a crash reachable purely from an attacker-supplied participant index.

### Finding Description
`Participant::new` accepts any nonzero `u16`, so a party can name a "sender" index far outside the actual validator set (e.g. `Participant(0xFFFF)` in a 5-of-9 DKG). `BlameMachine::blame` and `AdditionalBlameMachine::blame` pass `sender` straight to `blame_internal`, which does:

```rust
// crypto/dkg/pedpop/src/lib.rs:599
multiexp_vartime(&share_verification_statements::<C>(
  recipient,
  &self.commitments[&sender],   // panics if sender is not a key
  Zeroizing::new(share),
))
```

`HashMap`'s `Index` impl panics on a missing key. There is no membership check on `sender` (or `recipient`) anywhere in `blame`/`blame_internal`. The same applies on the `Decryption` path: `decrypt_with_proof(sender, ...)` looks up per-participant encryption state keyed by the same unvalidated index.

This is especially relevant for `AdditionalBlameMachine`, whose entire purpose is evaluating blame assertions submitted by third parties:

```rust
// crypto/dkg/pedpop/src/lib.rs:649-661
pub fn new(
  context: [u8; 32],
  n: u16,
  mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
) -> Result<Self, PedPoPError<C>> {
  ...
  for i in 1 ..= n {
    let i = Participant::new(i).unwrap();
    let Some(msg) = commitment_msgs.remove(&i) else { Err(...) };
    commitments.insert(i, encryption.register(i, msg).commitments);
  }
```

`commitments` only ever contains keys `1..=n`. Any `blame()` call with `sender > n` (or `sender` naming a removed/non-participating validator) is an unconditional panic — exactly the QEMU pattern of a guest-controlled index used to index a fixed-size structure with no bounds check.

Note the contrast with the in-protocol path: `KeyMachine::calculate_share` (`crypto/dkg/pedpop/src/lib.rs:468-491`) validates the `shares` map via `validate_map` against `all_participant_indexes()` before indexing `self.commitments[&l]`, so it is safe. The blame path skipped the equivalent validation.

### Impact Explanation
A single crafted blame/accusation message naming a `sender` (or `recipient`) outside `1..=n` crashes the process evaluating it. For `AdditionalBlameMachine` — explicitly designed to be usable by parties who were not DKG members — this means any unauthenticated observer or any participant can kill every node that evaluates their blame assertion, aborting the DKG/blame protocol and denying service. It is a pure-availability bug: no secret leakage, matching the CVE's DoS profile (CVSS A:H, Medium).

### Likelihood Explanation
- Reachability: `blame()` takes `sender`/`recipient` from the caller, which in any deployment derives from an accusation message the accusing party authors. `Participant` has no upper bound tied to `n` — `Participant::new` only rejects zero.
- No prerequisites: the accuser does not need a valid share, proof, or even protocol membership; the panic fires before any cryptographic check completes (`self.commitments[&sender]` is evaluated to build the verification statements).
- Caveat: an integrator *could* guard `blame()` calls by validating `sender <= n` themselves, but nothing in the API signals that requirement, and the library otherwise validates attacker-controlled indexes (`validate_map`, `ThresholdParams::new`, `view`). Uncertainty remains only on whether a given deployment exposes `blame` to unvalidated indexes.

### Recommendation
In `blame_internal` (and `AdditionalBlameMachine::new`/`Encryption` lookups), reject `sender`/`recipient` not present in `self.commitments` before indexing: e.g., `let Some(sender_commitments) = self.commitments.get(&sender) else { return sender /* or a dedicated InvalidParticipant error */ };`. Alternatively, record `n` in the machines and bound-check `u16::from(sender) <= n` up front, matching the `DkgError::InvalidParticipant` convention used elsewhere in `crypto/dkg`.

### Proof of Concept
```rust
// Setup: honest DKG completes for params t=2, n=3 producing a BlameMachine,
// or equivalently an AdditionalBlameMachine::new(context, 3, msgs).

// Attacker submits a blame assertion naming a participant index that was
// never part of the set. Participant::new only rejects 0.
let bogus_sender = Participant::new(0xFFFF).unwrap();

// Any syntactically valid EncryptedMessage<_, SecretShare<_>> suffices;
// the panic happens while building verification statements.
let (_machine, _faulty) = blame_machine.blame(
    bogus_sender,
    honest_recipient,
    msg,
    proof,
);
// PANIC: `self.commitments[&bogus_sender]` indexes a HashMap whose only
// keys are 1..=3 -> "key not found" panic -> denial of service.
```

Key code references:

- Unchecked index: `crypto/dkg/pedpop/src/lib.rs:599` (`self.commitments[&sender]` inside `blame_internal`)
- Map population limited to `1..=n`: `crypto/dkg/pedpop/src/lib.rs:656-660` (`AdditionalBlameMachine::new`)
- Public entry points lacking validation: `crypto/dkg/pedpop/src/lib.rs:623-632` (`BlameMachine::blame`) and `crypto/dkg/pedpop/src/lib.rs:674-682` (`AdditionalBlameMachine::blame`)
- Contrast — validated indexing elsewhere: `crypto/dkg/pedpop/src/lib.rs:468-491` (`validate_map` before `self.commitments[&l]` in `calculate_share`)