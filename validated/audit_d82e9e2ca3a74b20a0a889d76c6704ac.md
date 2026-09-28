### Title
Missing-participant panic in `GeneratorPromotion::complete` allows unauthenticated DoS via crafted proof map - ([File: crypto/dkg/promote/src/lib.rs])

### Summary
`GeneratorPromotion::complete` verifies the *quantity* and *upper bound* of participant indexes in the `proofs` map, but never verifies the map actually contains an entry for every `i` in `1 ..= n`. It then indexes the map with `.unwrap()`, panicking on a missing key. A participant who submits a promotion-proof set keyed on the completer's own index (instead of some other participant's) crashes the completing party.

### Finding Description
In `GeneratorPromotion::complete` (crypto/dkg/promote/src/lib.rs:120-156), two validation passes run over the attacker-influenced `proofs: &HashMap<Participant, GeneratorProof<C1>>`:

- `proofs.len() != params.n() - 1` is rejected (line 125).
- Any key `> params.n()` is rejected (lines 132-136).

Then the loop `for i in 1 ..= params.n()` skips `params.i()` and does `proofs.get(&i).unwrap()` at line 146. Since `params.i()` is always `<= n`, a map containing `{params.i()} ∪ {n-2 other valid indexes}` has length `n-1` and all keys `<= n`, passes both checks, yet is missing one required participant `j`. When the loop reaches `j`, `proofs.get(&j)` returns `None` and `.unwrap()` panics — the exact analog of the report's "missing field → nil dereference" bug class: structural validation was performed on the map's size and key range, but membership of each required key was assumed, not checked.

The `GeneratorProof<C1>` values are read from untrusted bytes via `GeneratorProof::read` (crypto/dkg/promote/src/lib.rs:72-77), which calls `C::read_G` and `DLEqProof::read` — both squarely in the permitted input surface — and the map keys are attacker-chosen `Participant` values supplied by the sending party.

### Impact Explanation
Any party supplying promotion proofs to a completing participant can crash that participant's process at will, before any proof verification occurs (the panic precedes `proof.proof.verify`). This is a complete denial of service of the generator-promotion protocol run: the completing node aborts and cannot produce `ThresholdKeys<C2>`. It is unrecoverable within `complete` because `.unwrap()` panics rather than returning `PromotionError`. Severity: Medium — a reliable unauthenticated crash, but scoped to the promotion ceremony rather than persistent funds or key compromise.

### Likelihood Explanation
The trigger requires no valid cryptography: the attacker only assembles a `proofs` map of length `n-1` whose keys omit one `i ∈ 1..=n` (e.g., by including an entry keyed as `params.i()`, duplicating under another index after serialization, or omitting any participant). Both guard conditions are satisfied, so the panic is deterministic. The only mitigating factor is that exploitation requires being a participant in the promotion protocol (or a caller that forwards unvalidated peer maps), hence Medium rather than High.

### Recommendation
Replace the unchecked indexing with an error return, mirroring the report's suggested nil-check fix:

```rust
let proof = proofs.get(&i).ok_or(PromotionError::IncorrectAmountOfParticipants {
  t: params.n(),
  n: params.n(),
  amount: proofs.len() + 1,
})?;
```

Optionally also reject `i == params.i()` appearing in `proofs` keys (an `InvalidParticipant`/`DuplicatedParticipant` style error), since the completer's own proof is supplied from `self.proof` and a foreign entry under that index is meaningless.

### Proof of Concept
```rust
// Setup: n-of-n keys, participant `i` completing promotion.
// `proofs` is built from bytes read via GeneratorProof::read for each peer.
let n = params.n();
let me = params.i();

// Attacker supplies n-1 entries: all valid participants except `j`,
// plus a spurious entry keyed as `me` (the completer's own index).
let j = /* some participant != me */;
let mut proofs = HashMap::new();
proofs.insert(me, attacker_proof);          // wrong key, still <= n
for k in 1 ..= n {
  let k = Participant::new(k).unwrap();
  if k == me || k == j { continue; }
  proofs.insert(k, proof_for(k));
}
assert_eq!(proofs.len(), usize::from(n) - 1); // passes length check
assert!(proofs.keys().all(|k| u16::from(*k) <= n)); // passes range check

promoting.complete(&proofs); // panics at `proofs.get(&j).unwrap()` (line 146)
```

Result: `thread panicked at 'called Option::unwrap() on a None value'` inside `GeneratorPromotion::complete` before any DLEq verification — a deterministic, remote-triggerable denial of service from public, well-formed-length input.