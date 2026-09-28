### Title
Panic via missing-index unwrap in `GeneratorPromotion::complete` when a proof is keyed to the local participant - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` validates only the length (`n - 1`) and the upper bound (`<= n`) of the peer-supplied `proofs` map, then iterates all indexes `1..=n` and unconditionally `unwrap()`s `proofs.get(&i)`. If a participant submits a `proofs` map that contains an entry keyed under the verifier's own index (or otherwise omits a required index while keeping the length at `n - 1`), the lookup returns `None` and the `unwrap()` panics — a remotely triggerable denial of service analogous to the unchecked-`NULL`-return dereference in CVE-2019-19227.

### Finding Description
The `HashMap<Participant, GeneratorProof<C1>>` passed to `complete` is built from messages received from other participants. The checks at `crypto/dkg/src/../promote/src/lib.rs:125-136` enforce only:
1. `proofs.len() == n - 1`
2. every key `<= n`

They do **not** enforce that the keys are exactly the set `{1..=n} \ {self.i}`. Because `HashMap` keys are unique, a map containing `n - 1` entries where one key equals `params.i()` necessarily omits some legitimate index `j` in `1..=n`. The loop then reaches `proofs.get(&j).unwrap()` at line 146 and panics. [1](#0-0) 

### Impact Explanation
An unprivileged DKG participant who supplies a malformed `proofs` map causes the victim's key-promotion (generator-rotation) run to panic instead of returning a `PromotionError`. Since `complete` consumes `self` and promotion is a one-shot protocol step, this aborts the promotion protocol for the victim — denial of service on a public-input-reachable path (`PromotionError::InvalidProof`/`InvalidParticipant` should have been returned instead). This matches the CVE-2019-19227 class: a fallible lookup/return (`proofs.get`, like `register_snap_client` returning `NULL`) used without handling the failure case, yielding a crash rather than a graceful error.

### Likelihood Explanation
Likelihood is moderate: exploitation only requires a DKG participant to send a well-formed message whose map keys are subtly wrong — no cryptographic break, no collusion, no key material needed. Impact is limited to crashing/aborting the promotion protocol rather than key recovery or forgery, so it lands at Medium under this scan's severities, consistent with the source CVE's availability-only 5.5 rating.

### Recommendation
Replace the unconditional `unwrap()` at `crypto/dkg/promote/src/lib.rs:146` with a checked lookup:

```rust
let proof = proofs
  .get(&i)
  .ok_or(PromotionError::IncorrectAmountOfParticipants {
    t: params.n(),
    n: params.n(),
    amount: proofs.len(), // or a dedicated MissingProof(i) variant
  })?;
```

Additionally, pre-validate the key set up front: reject any `proofs` entry keyed `params.i()` and confirm `proofs.keys()` equals `{1..=n} \ {i}` before iterating.

### Proof of Concept
Setup: `n = 3`, `t = 2`, local participant `i = 1`. After `GeneratorPromotion::promote`, the local node builds `proofs` from received `GeneratorProof`s. A malicious participant 2 sends a proof but registers it under key `Participant(1)` (the victim's index); participant 3 sends nothing. Resulting map: `{1: proof_from_2, 2: proof_from_2}` — `len() == 2 == n - 1`, all keys `<= 3`, passing both checks. The loop iterates `i = 2` (present), then `i = 3`: `proofs.get(&Participant(3))` is `None` → `unwrap()` panics, crashing the promotion instead of returning `Err(PromotionError::…)`.

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L125-146)
```rust
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

    let mut verification_shares = HashMap::new();
    verification_shares.insert(params.i(), self.proof.share);
    for i in 1 ..= params.n() {
      let i = Participant::new(i).unwrap();
      if i == params.i() {
        continue;
      }

      let proof = proofs.get(&i).unwrap();
```
