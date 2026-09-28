### Title
`GeneratorPromotion::complete` panics on malformed participant set instead of returning an error - (File: crypto/dkg/promote/src/lib.rs)

### Summary
The TensorFlow advisory is a `CHECK`-failure class bug: an input (`ksize`) that must satisfy a validity constraint is not checked, and a violating value causes a hard abort rather than a handled error. The same class exists in `crypto/dkg/promote`. `GeneratorPromotion::complete` validates only the length of the `proofs` map (`n - 1` entries) and that every key is `<= n`, but never checks that the map contains exactly the required set `1..=n` minus the local participant `params.i()`. A map containing the caller's own index satisfies both checks yet omits a required participant, and the subsequent `proofs.get(&i).unwrap()` panics. [1](#0-0) 

### Finding Description
`complete` takes `proofs: &HashMap<Participant, GeneratorProof<C1>>`, populated from promotion messages received from other participants. Validation is:

1. `proofs.len() == n - 1` (line 125).
2. Every key `<= params.n()` (lines 132-136).

Neither check ensures all required indexes `1..=n \ {params.i()}` are present. Because `Participant` keys are unique in a `HashMap`, a set of `n - 1` keys that includes `params.i()` necessarily excludes some required participant `j`. The loop at lines 140-156 then iterates `1..=n`, skips `params.i()`, and executes `proofs.get(&i).unwrap()` at line 146, which panics on the missing `j`. [2](#0-1) 

This is the same shape as the referenced bug: a collection derived from external messages is checked for size and per-element range, but a semantic invariant (exact set membership / positivity) is unchecked, and the violating input hits an unconditional abort (`unwrap`/`CHECK`) rather than the function's `Result` error path (`PromotionError::InvalidParticipant` / `MissingParticipant`-style variants exist in the sibling `dkg`/`pedpop` crates but are not used here). [3](#0-2) 

### Impact Explanation
The panic aborts the calling process/thread during generator promotion (e.g., key rotation between two ciphersuites sharing a curve). Any node running promotion crashes on receipt of a malformed proof set, denying service to that participant and stalling the key-update protocol. This is a remote, input-triggered availability failure matching the Medium-severity DoS profile of the reference advisory.

### Likelihood Explanation
Triggering requires the attacker to cause a `proofs` map containing `params.i()` (the victim's own index) or otherwise missing a required key while keeping length `n - 1`. Whether a malicious peer can do this depends on how the integrator attributes indexes to incoming messages; if indexes are taken from message contents or a mislabeled sender entry is accepted, a single faulty participant suffices. The panic path is reachable purely through the public `complete` API with adversary-influenced map contents. The required malformed input is trivial to construct (a correctly-sized map with the wrong key set), so the input itself imposes no difficulty once attribution is possible.

### Recommendation
Replace the implicit presence assumption with explicit membership validation, mirroring `validate_map` in `crypto/dkg/pedpop/src/lib.rs` (lines 57-83): after the length check, verify `proofs` does not contain `params.i()` and contains every `i in 1..=n` other than `params.i()`, returning a `PromotionError` (e.g., `InvalidParticipant`/`MissingParticipant`) instead of indexing. Concretely, change line 146 to propagate an error:

```rust
// crypto/dkg/promote/src/lib.rs
let proof = proofs.get(&i).ok_or(PromotionError::MissingParticipant(i))?;
```

and add a `MissingParticipant(Participant)` variant to `PromotionError`.

### Proof of Concept
```rust
// Assume n = 3, local participant i = 2, base ThresholdKeys<C1> already constructed.
let params = base.params(); // t=2, n=3, i=Participant(2)
let promotion = GeneratorPromotion::<C1, C2>::promote(&mut rng, base).0;

// Malformed map: n - 1 = 2 entries, all keys <= n, but includes the
// caller's own index (2) and omits required participant 1.
let mut proofs = HashMap::new();
proofs.insert(Participant::new(2).unwrap(), dummy_proof.clone()); // self index
proofs.insert(Participant::new(3).unwrap(), dummy_proof);         // valid range key

// Passes len check (2 == n-1) and range check (all <= 3),
// then panics at proofs.get(&Participant(1)).unwrap() on line 146.
let _ = promotion.complete(&proofs); // thread panics: called `Option::unwrap()` on a `None`
```

The panic occurs at `crypto/dkg/promote/src/lib.rs:146` inside `GeneratorPromotion::complete`. [4](#0-3)

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L125-156)
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
