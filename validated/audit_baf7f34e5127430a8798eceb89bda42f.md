### Title
Missing-key panic in `GeneratorPromotion::complete` allows a peer to crash key promotion via a malformed proofs map - (File: crypto/dkg/promote/src/lib.rs)

### Summary
Analogous to CVE-2016-9191 (a drain/set-membership operation mishandled such that an attacker-controlled input causes a system hang/crash rather than a clean rejection), `GeneratorPromotion::complete` validates only the *count* and *maximum index* of the `proofs` map, then unconditionally `unwrap()`s `proofs.get(&i)` for every other participant index. A proofs map that substitutes the local participant's own index `i` for any other participant's index satisfies both checks yet leaves one required entry absent, producing a panic instead of an error.

### Finding Description
In `complete`, the two validation passes are:

```rust
// crypto/dkg/promote/src/lib.rs:125-136
if proofs.len() != (usize::from(params.n()) - 1) { ... }
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() { ... }
}
```

These guarantee only `proofs.len() == n - 1` and `every key <= n`. Because `HashMap` keys are unique, a map containing `n - 1` keys drawn from `1..=n` necessarily omits exactly one index — but nothing prevents the omitted index from being a required one (any `j != params.i()`), or prevents the map from containing `params.i()` itself, which is never expected in the map.

Later, the iteration assumes every `i != params.i()` is present:

```rust
// crypto/dkg/promote/src/lib.rs:140-146
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() {
    continue;
  }
  let proof = proofs.get(&i).unwrap();   // panics if i was the omitted key
  proof.proof.verify(...)
```

So a map containing `{params.i(), k1, ..., k_{n-2}}` (omitting some `j != params.i()`, including `params.i()`) passes both validations and panics at `proofs.get(&j).unwrap()` instead of returning `PromotionError::InvalidParticipant`/`MissingParticipant`. The protocol's own error type anticipates bad membership (`IncorrectAmountOfParticipants`, `InvalidParticipant`), so this panic is an unhandled reachable path, not a documented invariant.

Note the contrast with the correct pattern in the same codebase: `frost::validate_map` (crypto/frost/src/lib.rs:50-72) explicitly checks `map.contains_key` for every expected participant and rejects `DuplicatedParticipant` for the local index. `pedpop::calculate_share` and `verify_r1` route their peer-supplied maps through it; `promote::complete` does not.

### Impact Explanation
`complete` is executed while aggregating `GeneratorProof`s received from other participants — i.e., bytes/messages supplied by unprivileged counterparties. Any single peer who can submit or influence the proofs map (including a coordinator assembling proofs keyed by `Participant`) can force the honest party's node to panic, aborting the promotion and crashing the task. In a validator/processor deployment this is a remotely triggerable denial of service of the key-promotion flow — directly paralleling the CVE's "crafted input causes system hang via mishandled drain" class: the set is drained by index lookup rather than validated by membership.

### Likelihood Explanation
Medium. Triggering requires control of the `proofs` map's keys, which is realistic in any integration that deserializes `HashMap<Participant, GeneratorProof>` from peer messages (borsh/`Participant::deserialize_reader` accepts any nonzero `u16 <= n`, with no exclusion of `params.i()`). No cryptographic forgery is needed — only key selection in a length-correct map — and the failure is deterministic. Impact is limited to a crash/abort of promotion rather than key compromise, matching the source CVE's Medium/DoS profile.

### Recommendation
Replace the length+max checks with full membership validation before the `unwrap`, mirroring `validate_map`: reject if `proofs.contains_key(&params.i())` (`DuplicatedParticipant`-style error) and `Err(InvalidParticipant)`/`MissingParticipant` for any `j in 1..=n, j != params.i()` absent from `proofs`. Alternatively, replace `proofs.get(&i).unwrap()` with `.ok_or(PromotionError::InvalidParticipant { n: params.n(), participant: i })`.

### Proof of Concept
```rust
// Setup: n = 3 participants, we are participant 1.
let (promo, _our_proof) = GeneratorPromotion::<C1, C2>::promote(&mut rng, base_keys_p1);

// Malformed map: contains n-1 = 2 entries, all <= n, but includes our own
// index and omits participant 3.
let mut proofs = HashMap::new();
proofs.insert(Participant::new(1).unwrap(), proof_from_1); // our own index — unexpected
proofs.insert(Participant::new(2).unwrap(), proof_from_2);
// participant 3 omitted

// proofs.len() == n-1 passes; all keys <= n passes;
// loop reaches i == 3 -> proofs.get(&3).unwrap() panics.
promo.complete(&proofs);
```

Both guard conditions at crypto/dkg/promote/src/lib.rs:125-136 are satisfied, and the panic occurs at `proofs.get(&i).unwrap()` (line 146).