### Title
GeneratorPromotion::complete panics on a well-formed-but-miskeyed proofs map, permanently aborting key promotion - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` validates the `proofs` map only by length (`n - 1`) and by upper bound on each key (`<= n`), then unconditionally unwraps `proofs.get(&i)` for every required participant. Because the `HashMap` keys are attacker-influenced (each remote participant's `GeneratorProof` is deserialized from their network message via `GeneratorProof::read`), a map that passes both checks yet omits a required index causes a panic instead of a returned `PromotionError`. This is the direct analog of the LayerZero "blocked queue / no nonblocking receiver" bug class: one malformed/misdirected message crashes the promotion, which is a required step in key rotation, instead of being rejected gracefully and allowing retry/blame.

### Finding Description
In `crypto/dkg/promote/src/lib.rs`:

```rust
if proofs.len() != (usize::from(params.n()) - 1) { ... IncorrectAmountOfParticipants ... }
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() { ... InvalidParticipant ... }
}
...
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() { continue; }
  let proof = proofs.get(&i).unwrap();   // line ~146: panic
``` [1](#0-0) 

The two guard checks establish only: (a) exactly `n - 1` entries, (b) every key `<= n`. They do not establish that the keys are exactly `1..=n` minus `params.i()`. A map containing `params.i()` itself (the local participant's own index) plus `n - 2` other valid indexes satisfies both checks while missing one required participant `j`. The loop then reaches `proofs.get(&j).unwrap()` and panics.

How the map reaches this state from public inputs: `GeneratorProof` is a per-participant network message, read from peer bytes (`GeneratorProof::read`, used in tests at `crypto/dkg/promote/src/tests.rs:90` over serialized bytes). The integrator builds `HashMap<Participant, GeneratorProof>` keyed by the sender index carried/claimed per message — the same pattern used everywhere else in the DKG stack (`shares: HashMap<Participant, EncryptedMessage<..>>` in `calculate_share`). A faulty/malicious participant whose message is filed under an index that is already occupied by the local participant (`params.i()`) — or a session where the local node's own proof is inserted into the same map — produces the miskeyed map. `PedPoP`'s equivalent entry point defends against exactly this with `validate_map`, which verifies the map keys equal the expected participant set: [2](#0-1) 

`GeneratorPromotion::complete` lacks any equivalent exact-set check, so the fault is detected only via a panic.

### Impact Explanation
A panic in `complete` aborts the entire generator-promotion protocol for the local node with no `Err` return, no identification of the faulty participant, and no way to resume. Since promotion runs with all `n` participants (`proofs.len() == n - 1` required) and is on the critical path for rotating keys to a new generator, one poisoned/misattributed message causes a hard denial of service of the rotation — mirroring the "single bad message blocks the queue with no recovery path" consequence described in the LayerZero report, except worse: it is a crash, not just a stuck queue. In a tokio/async deployment an unwinding panic can additionally kill the hosting task.

### Likelihood Explanation
Triggering requires the caller's proofs map to contain `params.i()` as a key. That happens if (a) the integrator inserts the node's own `GeneratorProof` into the map before calling `complete` (a natural implementation error the API does nothing to prevent — `tests.rs` explicitly does the opposite with `clone_without(&proofs, &i)`), or (b) a malicious participant's message is filed under the local index because authentication/index assignment is delegated to the caller, which the crate itself acknowledges is an external assumption. Either way, the defect is real: the function's own validation is insufficient for the unwraps it performs, so a `Result`-returning public API can panic on inputs that pass all of its checks. Severity is bounded to availability (Medium), consistent with the report class; there is no forgery or key leakage.

### Recommendation
Replace the `proofs.get(&i).unwrap()` with an explicit membership check returning `PromotionError::MissingParticipant(i)` (or reuse `dkg::validate_map` to assert `proofs.keys()` equals `all_participant_indexes() \ {params.i()}` up front), and additionally reject `proofs.contains_key(&params.i())`. This makes every malformed input a catchable error — the "nonblocking receive" equivalent — so the caller can attribute blame and retry rather than crash.

### Proof of Concept
```rust
// Setup: n-of-n keys as in crypto/dkg/promote/src/tests.rs, PARTICIPANTS = 5.
// promotions[i] is participant i's GeneratorPromotion; proofs map of all proofs.

// Poisoned map: insert OUR OWN index's proof, drop participant 2's proof.
// Map still has exactly n - 1 = 4 entries, all keys <= n -> passes both checks.
let i_local = Participant::new(1).unwrap();
let mut poisoned = clone_without(&proofs, &Participant::new(2).unwrap());
// poisoned now contains keys {1 /* == params.i() */, 3, 4, 5}: len 4, all <= 5.

// Panics at crypto/dkg/promote/src/lib.rs `let proof = proofs.get(&i).unwrap();`
// when the loop reaches i == Participant(2).
let _ = promotions[&i_local].complete(&poisoned); // thread panics; no Err returned
```

Uncertainty note: the panic is unconditional once the map shape is reached; the only assumption is that the caller can construct such a map, which the API permits since keys are caller-chosen `Participant` values and the function performs no exact-set validation — unlike the sibling `calculate_share` API which does.

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

**File:** crypto/dkg/pedpop/src/lib.rs (L463-472)
```rust
  pub fn calculate_share<R: RngCore + CryptoRng>(
    mut self,
    rng: &mut R,
    mut shares: HashMap<Participant, EncryptedMessage<C, SecretShare<C::F>>>,
  ) -> Result<BlameMachine<C>, PedPoPError<C>> {
    validate_map(
      &shares,
      &self.params.all_participant_indexes().collect::<Vec<_>>(),
      self.params.i(),
    )?;
```
