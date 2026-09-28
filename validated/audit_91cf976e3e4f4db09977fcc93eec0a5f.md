### Title
Missing exclusion of own participant index in `GeneratorPromotion::complete` causes reachable panic / DoS on crafted proof set — ([File: crypto/dkg/promote/src/lib.rs](crypto/dkg/promote/src/lib.rs))

### Summary
`GeneratorPromotion::complete` validates the incoming `proofs` map only by length (`n - 1`) and by an upper bound on each key (`<= n`). It never rejects a proof keyed under the local participant's own index (`params.i()`). A peer who submits a `proofs` map containing `params.i()` therefore satisfies both checks while omitting some required participant `j`, causing `proofs.get(&j).unwrap()` at line 146 to panic. This is a remotely triggerable crash (availability loss) during generator promotion, matching the CVE-2022-21517 bug class: a crafted protocol input causes a complete DoS of the processing node.

### Finding Description
The validation in `complete` is:

```rust
if proofs.len() != (usize::from(params.n()) - 1) { ... }          // line 125
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() { ... }                            // line 132-136
}
``` [1](#0-0) 

Both conditions can hold even when `proofs` contains the key `params.i()` and is missing a legitimate participant `j != params.i()`. The subsequent loop skips `params.i()` and unconditionally unwraps:

```rust
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() { continue; }
  let proof = proofs.get(&i).unwrap();                            // line 146 — panic
``` [2](#0-1) 

Contrast with FROST's `validate_map`, which explicitly errors on `DuplicatedParticipant` when the map contains `ours` (`crypto/frost/src/lib.rs:59-63`) — that defensive pattern is absent here. Note this is a real panic on library code expected to return `Result`, not a documented caller obligation: the docstring says "Complete promotion by taking in the proofs from all other participants" without warning that an excess/foreign key aborts the process. [3](#0-2) 

### Impact Explanation
Any crash of the node/process executing `GeneratorPromotion::complete` is a complete availability failure for that signing operation, and in a hosted context (processor/key-gen daemon) kills the whole process, not just the promotion session. In a consensus-relevant pipeline, a single crafted `HashMap` of `GeneratorProof`s from a peer halts key promotion permanently each time it is retried, blocking rotation/promotion of the group key. This is the Serai analog of CVE-2022-21517's "easily exploitable ... hang or frequently repeatable crash" pattern — a repeatable crash driven by attacker-supplied protocol data.

### Likelihood Explanation
`proofs` is assembled entirely from `GeneratorProof` messages received from other participants — i.e., bytes/messages an unprivileged peer causes the node to process, which the rules allow. The attacker does not need valid DLEq proofs or a key share; they only need to inject one `GeneratorProof` entry keyed at the victim's index (a pure `Participant` key, no cryptographic validity required since the key set is checked before proofs are verified). Reachability requires only that promotion be attempted — one malicious message, deterministic crash. Severity is Medium because it is DoS-only with no secret leakage, matching the source advisory's C:N/I:N/A:H profile.

### Recommendation
Reject any proof keyed at `params.i()` before consuming the map:

```rust
if proofs.contains_key(&params.i()) {
  Err(PromotionError::InvalidParticipant { n: params.n(), participant: params.i() })?;
}
```

or, more robustly, iterate `1 ..= params.n()` skipping `params.i()` and use `proofs.get(&i).ok_or(PromotionError::MissingParticipant(i))?` instead of `unwrap()`. The map should be validated to be exactly the set `{1..=n} \ {params.i()}` before any verification is attempted.

### Proof of Concept
With `n = 5` and local index `params.i() = 3`, a peer constructs:

```rust
let mut proofs = HashMap::new();
proofs.insert(Participant::new(3).unwrap(), arbitrary_proof); // victim's own index
for i in [1u16, 2, 4] {                                       // 5 is omitted
  proofs.insert(Participant::new(i).unwrap(), arbitrary_proof);
}
// proofs.len() == 4 == n - 1 ✓; all keys <= n ✓
promotion.complete(&proofs); // loops i = 5 -> proofs.get(&5).unwrap() -> PANIC
```

No `GeneratorProof` content is ever verified before the panic, so `arbitrary_proof` can be garbage bytes deserialized via `GeneratorProof::read` with syntactically valid encodings. Every retry with the same crafted set crashes again — a repeatable, network-triggerable DoS in `crypto/dkg/promote`.

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L124-136)
```rust
    let params = self.base.params();
    if proofs.len() != (usize::from(params.n()) - 1) {
      Err(PromotionError::IncorrectAmountOfParticipants {
        t: params.n(),
        n: params.n(),
        amount: proofs.len() + 1,
      })?;
    }
    for i in proofs.keys().copied() {
      if u16::from(i) > params.n() {
        Err(PromotionError::InvalidParticipant { n: params.n(), participant: i })?;
      }
    }
```

**File:** crypto/dkg/promote/src/lib.rs (L140-146)
```rust
    for i in 1 ..= params.n() {
      let i = Participant::new(i).unwrap();
      if i == params.i() {
        continue;
      }

      let proof = proofs.get(&i).unwrap();
```

**File:** crypto/frost/src/lib.rs (L59-70)
```rust
  for included in included {
    if *included == ours {
      if map.contains_key(included) {
        Err(FrostError::DuplicatedParticipant(*included))?;
      }
      continue;
    }

    if !map.contains_key(included) {
      Err(FrostError::MissingParticipant(*included))?;
    }
  }
```
