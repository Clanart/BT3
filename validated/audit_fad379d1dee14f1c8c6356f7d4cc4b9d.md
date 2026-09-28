### Title
`GeneratorPromotion::complete` panics on an unparseable/malformed participant set instead of returning an error — (File: crypto/dkg/promote/src/lib.rs)

### Summary
The bug class from the external report — improper input validation where malformed input produces an unhandled panic rather than an error result — is present in `GeneratorPromotion::complete`. The function validates the `proofs` map by checking only its length and that each key is `<= n`; it never checks that no key equals `params.i()` (our own index) nor that every required index is present. A `HashMap` containing the right *count* of entries but with one keyed under our own participant index (in place of some other required participant) passes both checks and then hits `proofs.get(&i).unwrap()`, panicking inside library code reachable from remote protocol messages.

### Finding Description
In `complete` (`crypto/dkg/promote/src/lib.rs:120-156`):

```rust
if proofs.len() != (usize::from(params.n()) - 1) { ... IncorrectAmountOfParticipants ... }
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() { ... InvalidParticipant ... }
}
...
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() { continue; }
  let proof = proofs.get(&i).unwrap();   // <-- panic on missing index
```

The two guards ensure `proofs.len() == n - 1` and every key `<= n`, but they do *not* ensure the keys are exactly `{1..=n} \ {params.i()}`. If the map contains an entry keyed under `params.i()` (which is meaningless — a participant cannot submit their own promotion proof) it counts toward `len`, displaces a genuinely required index, and `proofs.get(&missing_i)` returns `None`, causing `.unwrap()` to panic.

Compare `dkg`'s `validate_map` (`crypto/dkg/pedpop/src/lib.rs:57-83`) and FROST's `sign` (`crypto/frost/src/sign.rs:292-313`), which enumerate the *expected* included set and explicitly error on `MissingParticipant`/`DuplicatedParticipant`/`InvalidParticipant`. `promote` performs the strictly weaker count-plus-range check, leaving the `unwrap` reachable.

The `proofs` map is assembled from `GeneratorProof::read`-parsed messages sent by other participants over the network (`crypto/dkg/promote/src/lib.rs:72-77`); any reasonable integrator keys it by participant index, and nothing in the API prevents a remote party's proof from being recorded under a duplicate or self index. Like the Auth.js `getToken()` bug, the malformed input is treated as a panic instead of a routine `Err`, and callers who don't wrap `complete` in a panic handler crash on a single malformed protocol round.

### Impact Explanation
A single unauthenticated/malformed promotion message set causes an unconditional panic (`unwrap` on `None`) in `GeneratorPromotion::complete`, aborting the caller's key-promotion flow. This is a per-request availability failure only — no key material is recovered and no invalid key is accepted (a panic occurs before `ThresholdKeys::new`). Severity: Medium (availability, reachable by a remote party supplying protocol messages, no secret impact). This mirrors the advisory's CVSS profile (A:H, C/I:None), discounted here to Medium because the panic is confined to the promotion protocol rather than a general request handler.

### Likelihood Explanation
Triggering requires the `proofs` map to have `n - 1` entries with all keys `<= n` while missing some required index — which happens exactly when any entry is keyed under `params.i()` (or under a duplicated index if the caller deduplicates by insert). `Participant` values are attacker-influenced whenever the coordinator builds the map from received messages keyed by a self-declared or buggy index assignment, or when a faulty participant's message is slotted into the wrong map position. The library itself accepts any `HashMap<Participant, GeneratorProof>` and documented callers feed it remote data, so the condition is reachable with public inputs alone — no leaked keys or colluding threshold required.

### Recommendation
Replace the count/range checks with explicit membership validation: before the verify loop, reject if `proofs.contains_key(&params.i())`, and replace `proofs.get(&i).unwrap()` with `proofs.get(&i).ok_or(PromotionError::InvalidParticipant { n: params.n(), participant: i })?` (or a dedicated `MissingParticipant` variant). Alternatively reuse the `validate_map` pattern from PedPoP (`crypto/dkg/pedpop/src/lib.rs:57`) to check the key set equals `{1..=n} \ {i}` up front, returning an `Err` for every malformed set.

### Proof of Concept
For `n = 3`, victim `params.i() = 1`:

```rust
// proofs assembled from received GeneratorProof messages
let mut proofs = HashMap::new();
proofs.insert(Participant::new(1).unwrap(), proof_from_attacker); // keyed under victim's own index
proofs.insert(Participant::new(2).unwrap(), proof_from_p2);       // participant 3's proof absent

// Guards pass: len == 2 == n - 1, all keys <= 3
promotion.complete(&proofs); // panics at `proofs.get(&Participant(3)).unwrap()`
```

Concretely: `proofs.len() == 2` satisfies the `n - 1` check; keys `{1, 2}` are all `<= 3`; the loop skips `i == 1`, verifies `i == 2`, then calls `proofs.get(&3)` which returns `None` and `unwrap()` panics (`crypto/dkg/promote/src/lib.rs:146`). The malformed input produces a crash where a `PromotionError` was expected — the same improper-input-validation → unhandled-exception shape as `getToken()`'s percent-decode panic.