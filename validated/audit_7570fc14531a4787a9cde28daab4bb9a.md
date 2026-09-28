### Title
Panic in `GeneratorPromotion::complete` on proofs map containing the caller's own participant index - (File: crypto/dkg/promote/src/lib.rs)

### Summary

`GeneratorPromotion::complete` validates only the *count* (`proofs.len() == n - 1`) and the *upper bound* (`i <= n`) of the supplied proof map, then indexes it unconditionally with `proofs.get(&i).unwrap()` for every expected participant. If the map contains an entry keyed under the local participant's own index `params.i()` — displacing some legitimate participant `j` — the loop reaches `j`, finds no entry, and panics on `unwrap()`. This is an unprivileged-party-reachable crash analog to the referenced availability-only vulnerability class (CVE-2017-3456: easily exploitable input causing a repeatable crash / complete DoS). [1](#0-0) 

### Finding Description

`complete` takes `proofs: &HashMap<Participant, GeneratorProof<C1>>`, a map built from messages supplied by the other `n - 1` participants. Its validation is:

- `proofs.len() != n - 1` → error (`crypto/dkg/promote/src/lib.rs:125`)
- any key `> n` → error (`crypto/dkg/promote/src/lib.rs:132-136`)

It never rejects a key equal to `params.i()` and never verifies that the key set is exactly `{1..=n} \ {i}`. It then iterates `1 ..= n`, skips `params.i()`, and does `proofs.get(&i).unwrap()` (`crypto/dkg/promote/src/lib.rs:140-146`). A map like `{i, 2, 3}` for `n = 4, i = 1` passes both checks (3 entries, all ≤ 4) but is missing participant 4, so `proofs.get(&Participant(4)).unwrap()` panics.

By contrast, the same codebase does check set correctness elsewhere: `validate_map` in `crypto/frost/src/lib.rs:50-73` explicitly errors with `MissingParticipant`/`DuplicatedParticipant`, and `ThresholdKeys::view` in `crypto/dkg/src/lib.rs:463-491` validates membership and duplicates before indexing. `GeneratorPromotion::complete` performs neither check.

### Impact Explanation

A single malicious participant in a generator-promotion ceremony (used by Serai to promote threshold keys to a second generator, e.g. for a network requiring an alternate generator over the same curve) can crash every honest participant's `complete` call by publishing a `GeneratorProof` labelled with the victim's participant index, or by a relay/coordinator constructing the map such that the local index is present. Rust panics across an unwind boundary abort the promotion task; depending on the embedding, this can take down the processor's key-promotion path repeatedly (the input is trivially replayable — same crafted map, same deterministic panic), matching the "frequently repeatable crash" profile of the reference advisory. Availability impact only, consistent with the Medium CVSS class.

### Likelihood Explanation

The trigger requires only controlling one entry's key in the `proofs` map — a `Participant` label any participant can claim — plus `n - 1` total entries. No valid proof, no cryptographic break, and no collusion are needed; the panic occurs before any `DLEqProof::verify` is evaluated for the missing participant. The only mitigating factor is that the panic fires on the *missing* slot, so if the displaced participant's slot is iterated before verification of others, nothing secret is touched — the impact is purely a crash, which is precisely what this bug class requires.

### Recommendation

Before the unwrap loop, enforce set equality between `proofs.keys()` and `{1..=n} \ {params.i()}`, mirroring `validate_map` in `crypto/frost/src/lib.rs`. Concretely: reject any key equal to `params.i()` (return `PromotionError::InvalidParticipant`), and replace `proofs.get(&i).unwrap()` with an error return such as `Err(PromotionError::MissingParticipant)` (a new variant) or reuse `IncorrectAmountOfParticipants` semantics — the count check alone is insufficient.

### Proof of Concept

```rust
// n = 4, local participant i = 1 (t == n promotion)
let mut proofs = HashMap::new();
// Attacker supplies proofs under participants {1 (the victim's own index), 2, 3}
// instead of {2, 3, 4}. proofs.len() == 3 == n - 1, all keys <= n: both checks pass.
proofs.insert(Participant::new(1).unwrap(), some_proof);
proofs.insert(Participant::new(2).unwrap(), some_proof);
proofs.insert(Participant::new(3).unwrap(), some_proof);
// In complete(): loop hits i = 4, proofs.get(&Participant(4)) is None
// -> panic at crypto/dkg/promote/src/lib.rs:146: `.unwrap()` on None
promotion.complete(&proofs); // thread panic -> repeatable DoS
```

The panic site is `proofs.get(&i).unwrap()` at `crypto/dkg/promote/src/lib.rs:146`, reachable because the guard at lines 125–136 validates cardinality and range but not that the key set excludes `params.i()` or covers all other participants.

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
