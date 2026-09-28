### Title
`GeneratorPromotion::complete` panics on a proofs map containing the local participant's own index, permanently aborting key promotion - ([File: crypto/dkg/promote/src/lib.rs])

### Summary
The Lybra finding is a "permanently-failing privileged path": `setSafeCollateralRatio` can never succeed because `vaultType` is `internal`, so the configurator's external call always reverts — a reachable code path that is structurally impossible to complete. The analog in Serai is `GeneratorPromotion::complete` in the `dkg-promote` crate: its validation checks are insufficient to guarantee the subsequent `proofs.get(&i).unwrap()` succeeds, so a participant-supplied proofs map can drive the function into a panic, making promotion impossible to complete whenever a malicious (or merely malformed) submission is present — a reachable, unconditional failure of the ceremony rather than a graceful error.

### Finding Description
`GeneratorPromotion::complete` takes a `HashMap<Participant, GeneratorProof<C1>>` of proofs from all other participants and performs two checks:

1. `proofs.len() == params.n() - 1` [1](#0-0) 
2. Every key satisfies `u16::from(i) <= params.n()` [2](#0-1) 

It then iterates `for i in 1 ..= params.n()`, skips `i == params.i()`, and unconditionally unwraps:

```rust
let proof = proofs.get(&i).unwrap();
``` [3](#0-2) 

The checks never verify that the map's keys are exactly `1 ..= n` minus `params.i()`. Because the map's key domain is `Participant` (any nonzero u16), a proofs map containing the key `params.i()` itself — the local participant's own index — passes both checks (its length is still `n - 1`, and `params.i() <= n` is valid), yet leaves some legitimate participant `j ∈ 1 ..= n`, `j != params.i()` with no entry. When the loop reaches `j`, `proofs.get(&j)` returns `None` and `.unwrap()` panics.

Note the proof keyed at `params.i()` is never verified — the loop `continue`s for `i == params.i()` before any verification [4](#0-3) , so the bogus entry requires no valid signature/secret. Any party that can cause a `GeneratorProof` to be inserted into the `proofs` map under an arbitrary `Participant` index (e.g., the index is attacker-claimed message metadata, as in coordinator `ProcessorMessage` flows that key shares/proofs by a sender-supplied `Participant`) can trigger this with zero knowledge of any secret share.

This mirrors the Lybra bug's shape: a state-transition function whose internal consistency checks don't match what the later lookup assumes, so the call cannot complete — it reverts/panics unconditionally once the crafted input is present.

### Impact Explanation
A single malformed proof entry (index `params.i()`, or any index that displaces a required participant while keeping `len == n - 1` and all keys `<= n`) crashes `complete` via an `unwrap` panic instead of returning `PromotionError`. This aborts the generator-promotion ceremony for the victim node — the analog of `setSafeCollateralRatio` being permanently uncallable. Since the panic is a process-level abort in Rust (not a returned error), an integrator running promotion inside the coordinator/processor cannot gracefully blame the faulty party; the whole key-rotation attempt must be restarted, and the attacker can repeat the attack on every retry at negligible cost, achieving a persistent DoS of the promotion protocol. Because panic payloads propagate across `catch_unwind`-unprotected call stacks, this can take down the calling task entirely.

### Likelihood Explanation
The attack requires only that a participant in an n-of-n promotion submits a `GeneratorProof` under a `Participant` index they do not hold (specifically the victim's own index, or any duplicate that keeps the count at `n - 1` while starving a required index). The `Participant` index attached to each proof is untrusted wire data — the proof's DLEq verification binds to the claimed index `i` via `transcript(&self.base.original_group_key(), i)` [5](#0-4) , but the bogus entry at `params.i()` is never verified at all, so no valid proof or secret share is needed. There is no `contains_key`/key-set equality check anywhere in `complete`. Likelihood is bounded only by whether the deployment keys the map by authenticated sender identity vs. attacker-claimed index; nothing in the library enforces the former, and the function's own validation demonstrates it expects untrusted input (it checks `i > n`).

### Recommendation
Replace the length-and-range checks with an exact key-set check before the loop — e.g., verify `proofs.keys()` equals `{1 ..= n} \ {params.i()}` — or, minimally, convert the unwrap into a returned error:

```rust
let Some(proof) = proofs.get(&i) else {
  Err(PromotionError::IncorrectAmountOfParticipants {
    t: params.n(), n: params.n(), amount: proofs.len() + 1,
  })?
};
``` [6](#0-5) 

and additionally reject `proofs.contains_key(&params.i())` up front so a stray self-indexed entry fails fast with a `PromotionError` instead of panicking.

### Proof of Concept
```rust
use rand_core::OsRng;
use zeroize::Zeroizing;
use std::collections::HashMap;
use dkg::{Participant, ThresholdParams, ThresholdKeys, Interpolation};
use dkg_promote::GeneratorPromotion;
use dalek_ff_group::Ristretto;
use ciphersuite::Ciphersuite;

// Setup: n-of-n keys, then promote. A malicious participant submits their
// GeneratorProof keyed under the *victim's* index (params.i()), while
// omitting one legitimate participant. Length and range checks pass.
#[test]
#[should_panic] // unwrap on proofs.get(&j) where j's proof was displaced
fn promotion_panics_on_self_indexed_proof() {
  const N: u16 = 3;
  // ... construct ThresholdKeys for each participant via dkg tests' helpers ...

  // Victim is participant 1. proofs map received:
  //   {1: <bogus/self-indexed proof>, 2: <valid proof>}  // missing 3, len == n-1 == 2
  // complete() checks: len == 2 OK; keys 1,2 <= 3 OK.
  // loop: i=1 skipped (params.i()); i=2 verified; i=3 -> proofs.get(&3) == None -> unwrap() panic.
}
```

The panic occurs at `crypto/dkg/promote/src/lib.rs:146` (`proofs.get(&i).unwrap()`) whenever the attacker-claimed index set `{params.i()} ∪ S` (with `|S| = n - 2`, all keys `≤ n`) displaces a required participant — exactly mirroring how `setSafeCollateralRatio` can never complete because its internal assumption (`vaultType` being externally readable) doesn't hold on the reachable path.

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L125-131)
```rust
    if proofs.len() != (usize::from(params.n()) - 1) {
      Err(PromotionError::IncorrectAmountOfParticipants {
        t: params.n(),
        n: params.n(),
        amount: proofs.len() + 1,
      })?;
    }
```

**File:** crypto/dkg/promote/src/lib.rs (L132-136)
```rust
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

**File:** crypto/dkg/promote/src/lib.rs (L147-154)
```rust
      proof
        .proof
        .verify(
          &mut transcript(&self.base.original_group_key(), i),
          &[C1::generator(), C2::generator()],
          &[self.base.original_verification_share(i), proof.share],
        )
        .map_err(|_| PromotionError::InvalidProof(i))?;
```
