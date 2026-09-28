### Title
Missing key-set validation in `GeneratorPromotion::complete` causes a panic (availability DoS) on attacker-influenced proof maps - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` checks only the *length* of the `proofs` map (`n - 1` entries) and that each key is `<= n`, but never checks that the map's key set equals `{1..=n} \ {params.i()}`. It then unconditionally indexes `proofs.get(&i).unwrap()` for every participant `i != params.i()`. A map containing our own index (or any wrong-but-in-range set that omits one required participant) satisfies all checks yet hits `None.unwrap()` and panics, crashing the caller — a repeatable-crash availability failure, the direct analog of CVE-2018-3162's InnoDB complete-DOS class.

### Finding Description
In `crypto/dkg/promote/src/lib.rs:120-156`:

```rust
if proofs.len() != (usize::from(params.n()) - 1) { ... }
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() { ... }
}
...
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() { continue; }
  let proof = proofs.get(&i).unwrap();   // panic reachable
```

The two guard checks ensure `proofs` has `n-1` entries all `<= n`, but `n-1` in-range keys need not be the *correct* `n-1` keys. E.g., with `n = 3`, `params.i() = 2`, a map `{1, 2}` (which includes our own index and omits 3) passes both guards, then `proofs.get(&Participant(3))` returns `None` and `.unwrap()` panics. There is no error variant for this case; `PromotionError::InvalidParticipant` only covers `> n`. [1](#0-0) 

`Participant` cannot be 0 (`Participant::new` rejects zero), so the only failure mode is omission of a required participant, which is unhandled.

### Impact Explanation
Panic in library code invoked during a distributed key-promotion ceremony. `complete` is called once per participant with proofs gathered from all other participants; a single panic aborts the ceremony for that node and, repeated across nodes, prevents the generator promotion (and any dependent signing on the new generator) from completing. This is a "hang or frequently repeatable crash" availability impact matching the CVE's A:H profile. No secret material is leaked.

### Likelihood Explanation
The `proofs` map is assembled by the caller from messages received over the network, keyed by the `Participant` index associated with each proof. Any peer whose proof is registered under a wrong/missing index — including a peer that submits data the integrator stores under `params.i()`'s slot, or a session where one participant's proof never arrives but another's is duplicated under a valid in-range index — produces a key set that passes both guards and deterministically panics. Unlike a cryptographically hard trigger, this requires no computation beyond sending/omitting an index-labeled message. Caveat: reachability depends on how the integrator keys the map; the bug is that the library does not defend against an incorrect key set despite exposing `PromotionError` variants for adjacent mistakes.

### Recommendation
Replace the `.unwrap()` at line 146 with a proper error (e.g., extend `PromotionError` with a `MissingProof(Participant)` variant), and validate the key set up front: require `proofs.keys()` to equal `{1..=n} \ {params.i()}` exactly (reject our own index and any omitted participant) before iterating. Alternatively iterate `proofs` once, verifying each entry, and separately confirm `proofs` contains no key equal to `params.i()`.

### Proof of Concept
```rust
// n = 3, our participant index i = 2
let keys = /* ThresholdKeys<Ristretto> with ThresholdParams::new(3, 3, Participant(2)) */;
let (promotion, our_proof) =
  GeneratorPromotion::<Ristretto, AltGenerator<Ristretto>>::promote(&mut OsRng, keys);

let mut proofs = HashMap::new();
// Attacker-/network-influenced map: contains our own index, omits participant 3.
proofs.insert(Participant::new(1).unwrap(), valid_proof_from_1);
proofs.insert(Participant::new(2).unwrap(), our_proof /* or peer-supplied */);

// proofs.len() == 2 == n - 1, all keys <= n: guards pass.
// Loop reaches i = 3 -> proofs.get(&3) == None -> unwrap() panics.
promotion.complete(&proofs);
```

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
