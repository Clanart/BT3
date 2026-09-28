### Title
Unprivileged participant triggers panic (DoS) in `GeneratorPromotion::complete` by keying a proof to the victim's own index - ([File: crypto/dkg/promote/src/lib.rs])

### Summary
`GeneratorPromotion::complete` validates only that the number of proofs equals `n - 1` and that no participant index exceeds `n`. It never checks that every `i` in `1 ..= params.n()` (except `params.i()`) actually has a proof. A malicious participant can therefore submit its proof map keyed under the victim's own `params.i()` (duplicating coverage of itself and omitting another participant), causing `proofs.get(&i).unwrap()` to panic and crash the victim's promotion — an availability failure reachable purely from attacker-controlled inputs.

### Finding Description
The bug class of the reference report (remotely triggerable complete DoS via crafted inputs to a service component) maps onto Serai's generator-promotion path: [1](#0-0) 

The validation loop only enforces `proofs.len() == n - 1` and `i <= n` for each key. Since `HashMap` keys are unique, an attacker supplying a map keyed with `params.i()` (the victim) plus `n - 2` other participants satisfies both checks while leaving one honest participant `j` without a proof. The subsequent loop `for i in 1 ..= params.n()` then executes `proofs.get(&i).unwrap()` for `i == j`, panicking on `None`.

`Participant` only enforces non-zero (crypto/dkg/src/lib.rs `Participant::new`), so index `params.i()` is a perfectly valid key. Unlike `view()` in crypto/dkg/src/lib.rs (lines 463-491), which sorts and checks for duplicates and non-participation, `complete` performs no set-membership validation of the proof keys against the expected participant set.

### Impact Explanation
Any participant in a promotion ceremony can deterministically crash a peer running `GeneratorPromotion::complete`, halting the key-promotion flow (e.g., a Bitcoin/Schnorrkel generator transition). This is a complete, repeatable denial of service of that protocol execution — matching the "hang or frequently repeatable crash (complete DOS)" class of the reference advisory.

### Likelihood Explanation
Reachable by any unauthenticated participant able to send a `HashMap<Participant, GeneratorProof<C1>>` into `complete` — normal DKG/promotion message flow. No collusion, threshold compromise, or leaked keys required; just a malformed proof map.

### Recommendation
Validate the proof key set explicitly before iterating: require `proofs.keys()` to equal `all participants except params.i()` (e.g., reuse `validate_map`-style logic as in crypto/dkg/pedpop/src/lib.rs lines 57-83), and replace `proofs.get(&i).unwrap()` with a checked lookup returning `PromotionError::MissingParticipant(i)`.

### Proof of Concept
```rust
// Victim: params.n() = 3, params.i() = 2.
// Attacker submits proofs keyed {2, 3} (victim's index + participant 3),
// omitting participant 1. proofs.len() == n - 1 == 2 passes.
// All keys <= n passes.
// Loop hits i = 1: proofs.get(&Participant(1)) == None -> .unwrap() panics.
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
