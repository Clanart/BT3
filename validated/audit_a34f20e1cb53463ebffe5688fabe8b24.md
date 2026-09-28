### Title
Missing-participant panic in `GeneratorPromotion::complete` via attacker-controlled proofs map - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` validates only the length of the caller-supplied `proofs: HashMap<Participant, GeneratorProof<C1>>` and that each key is `<= n`. It never checks that the map keys are exactly the `n - 1` other participants. An entry keyed under the local participant's own index (`params.i()`), or any duplicated index, satisfies both checks while leaving some legitimate participant `j` absent. The subsequent `proofs.get(&i).unwrap()` on line 146 then dereferences a `None` and panics — the direct Rust analog of the NULL-pointer dereference in `find_cc()` (CVE-2021-33458): attacker-influenced input reaching a code path that unconditionally unwraps a missing value.

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
  let proof = proofs.get(&i).unwrap();   // panic if j missing
```

Checks performed:
- `proofs.len() == n - 1` (line 125)
- every key `<= n` (line 133)

Missing checks:
- key `!= params.i()` (self index is accepted)
- all `n - 1` required indexes are present (duplicates/self entries displace a real participant)

Because `HashMap` keys are attacker-selected — each participant's `GeneratorProof` is serialized over the wire and keyed by a participant index supplied by the coordinator/peer layer — a faulty or malicious participant (or a misdelivery that maps a proof to the wrong index) produces a map that passes validation yet lacks participant `j`. `proofs.get(&j)` returns `None` and `.unwrap()` aborts the process.

This is analogous to `GeneratorPromotion`'s sibling checks in `pedpop` (`validate_map`), which properly enforce the exact key set — `promote` lacks the equivalent.

### Impact Explanation
Availability loss (CVSS-consistent with the 5.5/A:H source bug). A single crafted `proofs` map — constructible entirely from public inputs (a `GeneratorProof` submitted under a wrong/duplicate `Participant` key) — crashes the promotion routine on every honest node that runs `complete`, aborting the generator-promotion ceremony and denying the key-rotation/alt-generator transition. Since `complete` consumes `self` and the panic unwinds before `ThresholdKeys::new`, the ceremony must be restarted; repeated poisoning keeps the set stuck.

### Likelihood Explanation
Reachable by an unprivileged party: `complete` takes a `HashMap` assembled from per-participant messages, and the only guard on keys is `<= n`. A peer who submits their proof so it is keyed as the victim's own index `i` (or any index already claimed) triggers the panic deterministically — no threshold collusion, no invalid curve points, no leaked secrets required. The panic path is the first `proofs.get(&j).unwrap()` for the displaced `j`.

### Recommendation
Replace the two ad-hoc checks with set-equality validation, e.g. require `proofs.keys().copied().collect::<BTreeSet<_>>() == (1..=params.n()).map(Participant).filter(|i| *i != params.i()).collect()`, or reuse the `validate_map`-style helper used by PedPoP. Return a `PromotionError::IncorrectAmountOfParticipants`/`InvalidParticipant` instead of panicking, and consider asserting `!proofs.contains_key(&params.i())` explicitly.

### Proof of Concept
```rust
// n = 3, local participant i = 1.
// Attacker's proof is keyed under index 1 (self) instead of 3.
let mut proofs: HashMap<Participant, GeneratorProof<Ristretto>> = HashMap::new();
proofs.insert(Participant::new(1).unwrap(), attacker_proof); // self index — not rejected
proofs.insert(Participant::new(2).unwrap(), honest_proof);   // len == n - 1 == 2 ✓

// complete() at crypto/dkg/promote/src/lib.rs:146:
//   loop i = 2: proofs.get(&2).unwrap() -> Ok
//   loop i = 3: proofs.get(&3).unwrap() -> None -> panic!
promotion.complete(&proofs); // thread panics; ceremony aborted
```

All validation (`proofs.len() == n - 1`, every key `<= n`) passes, and the unwrap on a missing participant index panics — a NULL-deref-equivalent denial of service driven purely by attacker-chosen map keys.