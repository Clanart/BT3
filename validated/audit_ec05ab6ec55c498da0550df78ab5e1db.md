### Title
`GeneratorPromotion::complete` validates proof count and bounds but not exact participant coverage, enabling a panic on untrusted input - (File: crypto/dkg/promote/src/lib.rs)

### Summary
The external report concerns a validator function that checks each parameter individually but misses a cross-condition, so a nominally "valid" input makes the intended operation fail irrecoverably. The same class appears in `GeneratorPromotion::complete` in `crypto/dkg/promote/src/lib.rs`: it validates the *number* of submitted proofs and that every key is `<= n`, but never checks that the map's key set equals the required participant set. A malformed `proofs` map that passes both checks reaches `proofs.get(&i).unwrap()` with a missing key and panics.

### Finding Description
`complete` performs two validations on the attacker-influenced `proofs: &HashMap<Participant, GeneratorProof<C1>>`: [1](#0-0) 

- `proofs.len() == n - 1` (count check)
- every key `i <= params.n()` (bound check)

It then iterates every participant `1 ..= n`, skipping `params.i()`, and unconditionally unwraps the proof for that participant: [2](#0-1) 

Because only the count and upper bound are checked, the map can omit a required participant `l` as long as it contains some other in-range key in its place — most trivially `params.i()` itself (the caller's own index, which the loop skips). For `n = 3`, `params.i() = 1`, the map `{1, 2}` has length `2 == n - 1` and all keys `<= n`, so both checks pass; the loop then reaches `i = 3`, `proofs.get(&3)` returns `None`, and `.unwrap()` panics. Equivalently, the map could contain a duplicate-effect key arrangement that leaves any required participant absent.

This is exactly the shape of the reference bug: the validation checks independent properties (length, bound) but not the actual invariant the later code relies on (set equality with `all_participant_indexes() \ {i}`), so an input accepted by the validator fails at use time — here with a panic, which is worse than a clean `Err`.

### Impact Explanation
`complete` is a public API consuming a map supplied by the other participants' `GeneratorProof`s (deserializable via `GeneratorProof::read`, and `complete` is an untrusted-input entry point). A single participant submitting a correctly-sized but incorrectly-keyed proof map crashes the caller's process during generator promotion. Since the promotion result is never produced, the affected node's key update aborts mid-protocol. Unlike the reference bug (which only cost the user gas and forced redeployment), here the missing check yields a remote, deterministic panic — a denial of service that can stall a key-rotation/promotion ceremony that all honest parties otherwise completed correctly.

### Likelihood Explanation
Any participant in a promotion ceremony can craft the malformed map: the keys are `Participant` indices under their control and the length/`<= n` checks are trivially satisfied while omitting one required index (e.g. by including the victim's own index `params.i()`, which the code itself skips). No collusion, leaked keys, or broken-BFT assumptions are needed — the input is supplied verbatim to `complete`. Exploitation requires a promotion ceremony to be in progress, which is a periodic protocol event rather than a continuous exposure.

### Recommendation
Replace the count/bound checks with an exact set-membership validation, matching the stricter pattern already used by `validate_map` in `crypto/dkg/pedpop/src/lib.rs`: [3](#0-2) 

Concretely, in `complete` before iterating: reject if `proofs.contains_key(&params.i())` (own index must not appear) and reject if any `i in 1..=n, i != params.i()` is missing from `proofs` — or simply index with `proofs.get(&i).ok_or(PromotionError::MissingParticipant(i))?` instead of `.unwrap()` so the failure returns a typed error rather than panicking. Keeping the `len() == n - 1` and `i <= n` checks alongside is fine, but they are not sufficient alone.

### Proof of Concept
```rust
// n = 3 participants, victim has params.i() == 1
let (promotion, our_proof) = GeneratorPromotion::<C1, C2>::promote(&mut rng, base_keys);

// Malicious peer 2 builds a proof map of length n-1 = 2,
// all keys <= n, but keyed wrongly: includes the victim's own
// index 1 and omits required participant 3.
let mut proofs = HashMap::new();
proofs.insert(Participant::new(1).unwrap(), peer2_proof); // victim's own index
proofs.insert(Participant::new(2).unwrap(), peer2_proof); // key <= n, passes bound check

// complete() accepts: proofs.len() == 2 == n - 1, all keys <= 3.
// Loop: i=2 verifies (or fails with InvalidProof if invalid —
// use two honestly-generated proofs from peer 2 re-keyed to make it pass);
// then i=3 -> proofs.get(&3) == None -> unwrap() panics.
promotion.complete(&proofs); // panic: called `Option::unwrap()` on a `None`
```

Concretely, at `crypto/dkg/promote/src/lib.rs:146`, `proofs.get(&i).unwrap()` is reached with `i = Participant(3)` absent from the map, panicking instead of returning `PromotionError`. The two guards at lines 125-136 (length and bound) both pass on the malformed input, demonstrating the missing cross-condition check.

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L125-136)
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
```

**File:** crypto/dkg/promote/src/lib.rs (L140-156)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L56-83)
```rust
// Validate a map of values to have the expected included participants
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
