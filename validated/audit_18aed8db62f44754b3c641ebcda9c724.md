### Title
Missing participant-set validation in `GeneratorPromotion::complete` lets a malformed proof map panic instead of erroring — (File: crypto/dkg/promote/src/lib.rs)

### Summary
The external report's bug class — an initialization/entrypoint that fails to validate its inputs and silently accepts incorrect values — maps onto `GeneratorPromotion::complete` in `crypto/dkg/promote/src/lib.rs`. The function validates only the *quantity* (`proofs.len() == n - 1`) and the *upper bound* of participant indexes (`i <= n`), but never checks that the map is keyed to the correct set of `n - 1` *other* participants. It then indexes the map with `.unwrap()`, so a malformed set that satisfies both checks reaches a panic on `HashMap::get`.

### Finding Description [1](#0-0) 

The two guards are:

```rust
if proofs.len() != (usize::from(params.n()) - 1) { ... }
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() { ... }
}
```

Both checks pass for a map like `{i_self} ∪ (others \ {j})` — i.e., one that contains the local participant's own index `params.i()` (which should be excluded, since `self.proof` is used for the local share at line 139) or any index `k <= n` that isn't actually a pending participant, while omitting a real participant `j`. The completion loop then does:

```rust
for i in 1 ..= params.n() {
  if i == params.i() { continue; }
  let proof = proofs.get(&i).unwrap();   // panics when i == j is absent
```

`proofs.get(&i).unwrap()` at line 146 panics for the missing `j`. Note that even a correctly-keyed insertion of a bogus proof is safe — `proof.proof.verify(...)` at line 149 returns `PromotionError::InvalidProof(i)` cleanly — so the only unvalidated input class is the key-set shape itself, exactly the "missing `require` on initializer inputs" pattern from the report.

A peer reaches this via a `GeneratorProof` message they broadcast during promotion (`GeneratorProof::read` accepts untrusted bytes). The `HashMap<Participant, GeneratorProof>` is assembled by the caller from received messages; a sender whose message is keyed under a participant index they don't own (e.g., the victim's own index `i_self`), or whose entry displaces an absent participant while preserving `len() == n - 1` and `i <= n`, produces a map that passes both guards and panics the victim's `complete` call.

### Impact Explanation
`complete` is the final step of generator promotion, the mechanism used to migrate an existing `ThresholdKeys` set to a new generator (`AltGenerator`-style suites share the same field/group). A panic aborts the promotion outright: the migrated `ThresholdKeys<C2>` are never produced, so funds/operations depending on the promoted key cannot proceed, and — importantly — the protocol aborts via crash rather than a blamable `PromotionError`, so the faulty/malicious participant is not cleanly identified. An unauthenticated-value input (the key-set of a peer-supplied map) causes a node-side panic: a remote, unprivileged denial of service on a key-management path.

### Likelihood Explanation
The map keying is caller-assembled from wire messages, and `GeneratorProof` itself carries no authenticated `Participant` field inside its verified payload — the index is only implicitly bound via `transcript(&group_key, i)` at verify time (line 150). Any integration that keys the map by a sender-claimed or session-ambiguous index is exposed. Even with correctly-authenticated keys, the validation gap remains latent: any construction of the map that includes `i_self` (e.g., collecting *all* broadcast proofs including one's own echo, then relying on `len()` to be correct — which holds when another participant is absent) hits the same panic. The checks as written only cover count and range, never membership, which is the exact input-validation omission.

### Recommendation
Before the `unwrap`, validate set membership explicitly, mirroring `validate_map` patterns used elsewhere (e.g., `crypto/dkg/pedpop/src/lib.rs` `validate_map`, `crypto/frost/src/lib.rs` `validate_map`):

```rust
if proofs.contains_key(&params.i()) {
  Err(PromotionError::InvalidParticipant { n: params.n(), participant: params.i() })?;
}
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i != params.i() && !proofs.contains_key(&i) {
    Err(PromotionError::MissingParticipant(i))?; // or reuse an existing error variant
  }
}
```

Then replace `proofs.get(&i).unwrap()` with a lookup that cannot panic on validated input, or keep the lookup infallible after the membership check.

### Proof of Concept
```rust
// Assume params: t = n = 3, local index i_self = 1.
// proofs contains keys {1, 2} -> len() == n - 1 == 2 passes the count check,
// all keys <= n passes the bound check, but participant 3 is missing and
// the local participant 1 is wrongly included.
let mut proofs: HashMap<Participant, GeneratorProof<C1>> = HashMap::new();
proofs.insert(Participant::new(1).unwrap(), attacker_proof_labeled_as_1);
proofs.insert(Participant::new(2).unwrap(), proof_from_2);

// Inside complete():
//   proofs.len() == 2 == n - 1     -> passes
//   all keys <= 3                  -> passes
//   loop: i = 1 skipped (i_self), i = 2 ok, i = 3 ->
//   proofs.get(&3).unwrap()        -> PANIC
promotion.complete(&proofs); // thread panics instead of returning PromotionError
```

No valid signature, share, or key material is required from the attacker — only a `GeneratorProof` message routed/keyed under a participant index that produces a `len() == n - 1`, `key <= n` map missing a real participant.

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
