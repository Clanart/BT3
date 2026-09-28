### Title
Missing-key `unwrap()` panic in `GeneratorPromotion::complete` allows a malformed participant map to crash key promotion - (File: crypto/dkg/promote/src/lib.rs)

### Summary
The CVE-2017-14865 bug class is "crafted input drives an out-of-bounds/invalid read that aborts the process (DoS)". Serai's analog is in the in-scope `crypto/dkg/promote` crate: `GeneratorPromotion::complete` iterates every participant `1..=n` (skipping self) and calls `proofs.get(&i).unwrap()`, panicking on any `Participant` that is not present in the caller-supplied `proofs` map — even though the map passed all of the function's own validation checks. A participant index equal to `params.i()` (the local node) is neither rejected nor required, so a map containing `n - 1` entries that includes `params.i()` and omits one real signer passes validation and then panics mid-loop. [1](#0-0) 

### Finding Description
`complete` performs two checks on the untrusted `proofs: &HashMap<Participant, GeneratorProof<C1>>`:

1. `proofs.len() == params.n() - 1` (lines 125–131)
2. every key `i` satisfies `u16::from(i) <= params.n()` (lines 132–136)

It then loops `for i in 1 ..= params.n()` skipping `params.i()` and does `proofs.get(&i).unwrap()` (line 146). Nothing enforces that the map keys are exactly `{1..=n} \ {i_self}`. A map keyed `{i_self, 1..=n} \ {j}` for any `j != i_self` has length `n - 1`, all keys `<= n`, and passes both checks — then `proofs.get(&j)` returns `None` and `.unwrap()` panics. This is the same `index-must-exist` pattern as `ThresholdKeys::original_verification_share` / `ThresholdView::verification_share` (`self.verification_shares[&l]`, crypto/dkg/src/lib.rs:458, 681), but here the lookup is on an *attacker-influenced* map inside a `complete` API, before the panic site is reached.

`GeneratorProof` itself is deserializable (proof share + `DLEqProof::read`, crypto/dleq/src/lib.rs:191), so the map contents and the participant key under which a proof is filed are reachable to an unprivileged counterparty in the promotion protocol. The panic aborts the promotion after some `verification_shares` have already been inserted, i.e. mid-state-machine, crashing the caller (Rust panic across the `complete` call).

### Impact Explanation
Denial of service of the key-promotion flow. Any party able to supply a `GeneratorProof` (or to control the `Participant` key under which their proof is inserted — e.g., a message carrying a self-declared index) can force `complete` to panic by having their proof filed under `params.i()` or by leaving the map one short while count-validation still passes. The victim's promotion aborts; depending on the caller's panic handling this kills the signing/key-update task for that multisig. The panic occurs before `ThresholdKeys::new` is reached, so no key material is corrupted — impact is availability, matching the CVE's crafted-input DoS class.

### Likelihood Explanation
Triggering requires `proofs.len() == n - 1` while the key set is not `1..=n` minus self — i.e., one entry keyed `params.i()` plus one real participant missing, or a duplicated/self-indexed submission. In a protocol where each participant's proof is keyed by a self-declared or relayed index, this is a single malformed/duplicate message. If the integrator strictly keys the map by authenticated sender identity, reachability is reduced — but the API itself performs no defense and documents no such requirement, and the function's own checks give a false sense of validation. Note this was not fully verifiable against every caller, as call sites live outside the in-scope crates.

### Recommendation
Replace the `unwrap()` at line 146 with a checked lookup returning `PromotionError`, and add an explicit key-set validation before the loop:

```rust
// crypto/dkg/promote/src/lib.rs, in GeneratorPromotion::complete
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() {
    if proofs.contains_key(&i) {
      Err(PromotionError::InvalidParticipant { n: params.n(), participant: i })?;
    }
    continue;
  }
  if !proofs.contains_key(&i) {
    Err(PromotionError::IncorrectAmountOfParticipants {
      t: params.n(), n: params.n(), amount: proofs.len() + 1,
    })?;
  }
}
```

Alternatively, reject `params.i()` in the existing key-range check loop and then use `proofs.get(&i).ok_or(PromotionError::MissingParticipant(i))?` (adding that error variant) instead of `unwrap()`.

### Proof of Concept
Conceptual: with `params = ThresholdParams::new(2, 3, Participant::new(1))` (self = participant 1, n = 3), build `proofs` with keys `{Participant(1), Participant(2)}` — length 2 = n−1, all keys ≤ 3, passes both checks. The loop skips `i = 1`, verifies participant 2, then `proofs.get(&Participant(3)).unwrap()` panics. Equivalently with keys `{1, 1}`-style filing under the victim's own index via a self-declared participant field in the promotion message.

No heap overflow exists in Serai's memory-safe Rust, but the reachable panic-on-untrusted-input path above is the direct analog of the Exiv2 crafted-input crash: the validation is incomplete for exactly the index that is later dereferenced unconditionally.

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L120-156)
```rust
  pub fn complete(
    self,
    proofs: &HashMap<Participant, GeneratorProof<C1>>,
  ) -> Result<ThresholdKeys<C2>, PromotionError> {
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

    let mut verification_shares = HashMap::new();
    verification_shares.insert(params.i(), self.proof.share);
    for i in 1 ..= params.n() {
      let i = Participant::new(i).unwrap();
      if i == params.i() {
        continue;
      }

      let proof = proofs.get(&i).unwrap();
      proof
        .proof
        .verify(
          &mut transcript(&self.base.original_group_key(), i),
          &[C1::generator(), C2::generator()],
          &[self.base.original_verification_share(i), proof.share],
        )
        .map_err(|_| PromotionError::InvalidProof(i))?;
      verification_shares.insert(i, proof.share);
    }
```
