### Title
`GeneratorPromotion::complete` panics on a proof map that duplicates the local participant index, omitting a required participant - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` validates only the *length* of the attacker-influenced `proofs` map (`proofs.len() == n - 1`) and that each key satisfies `i <= n`. It never checks that the map does not contain the local participant's own index `params.i()`. It then iterates `1 ..= n` and unconditionally `unwrap()`s `proofs.get(&i)` for every `i != params.i()`. A proof set that includes the victim's own index and omits a legitimate participant passes both checks yet triggers a panic on the `unwrap()` — the same bug class as the cgroup advisory: a looked-up object is consumed without checking it is of the expected kind/present.

### Finding Description
In `crypto/dkg/promote/src/lib.rs`, `complete` does:

```rust
if proofs.len() != (usize::from(params.n()) - 1) { ... IncorrectAmountOfParticipants ... }
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() { ... InvalidParticipant ... }
}
...
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() { continue; }
  let proof = proofs.get(&i).unwrap();   // panic if missing
``` [1](#0-0) 

Because `HashMap` keys are unique, a map containing `params.i()` plus `n - 2` other valid indexes has length `n - 1` and all keys `<= n`, so both guards pass — yet one `i in 1..=n, i != params.i()` is absent, and `proofs.get(&i).unwrap()` panics. The keys arrive from remote participants during the generator-promotion phase; each `GeneratorProof` is deserializable by any sender via `GeneratorProof::read` (`crypto/dkg/promote/src/lib.rs:72`), so the malformed map is reachable with public inputs. Compare with `ThresholdKeys::view` (`crypto/dkg/src/lib.rs:463-491`), which explicitly checks duplicates, membership, and bounds before indexing — the promote path lacks the equivalent completeness check.

### Impact Explanation
A single misbehaving participant can crash the victim node's promotion (and thus the surrounding key-management process) by substituting the victim's own participant index for a real peer's entry in the `proofs` map. This is a remotely triggerable panic/denial of service during a threshold-protocol run — analogous to the kernel panic caused by `cgroup_get_from_id()` failing to check the looked-up kernfs node kind.

### Likelihood Explanation
The malformed input is trivially constructed: any party capable of supplying `Participant -> GeneratorProof` entries (or influencing which proofs are forwarded) swaps one key for the victim's index. No collusion, secret knowledge, or expensive computation is required — only sending a structurally valid but semantically wrong map.

### Recommendation
Before iterating, verify `proofs` contains exactly the expected set: reject if `proofs.contains_key(&params.i())`, and replace `proofs.get(&i).unwrap()` with a checked lookup returning `PromotionError::InvalidParticipant`/a dedicated missing-proof error when `proofs.get(&i)` is `None`.

### Proof of Concept
```rust
// Setup: n participants promote their keys; victim = participant 1.
let params = victim_keys.params(); // t == n after musig-style promotion setup
let mut proofs: HashMap<Participant, GeneratorProof<C>> = HashMap::new();

// Instead of peer 2's proof, attacker submits an entry keyed as the victim.
proofs.insert(params.i(), attacker_proof);          // key = 1, the victim
for j in 3 ..= params.n() {                          // peers 3..=n legitimate
  proofs.insert(Participant::new(j).unwrap(), honest_proof(j));
}
// proofs.len() == n - 1  -> passes IncorrectAmountOfParticipants
// all keys <= n          -> passes InvalidParticipant
// participant 2 is absent -> proofs.get(&Participant::new(2).unwrap()) returns None

promoting.complete(&proofs); // PANIC: unwrap on None at promote/src/lib.rs:146
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
