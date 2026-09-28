### Title
`GeneratorPromotion::complete` panics on a proof map keyed to the local participant — host DoS from untrusted input - ([File: crypto/dkg/promote/src/lib.rs])

### Summary
The Wasmtime advisory describes a wrong-indexing bug where a `table.*` instruction referenced a nonexistent/mismatched table, letting a valid guest panic the host. The Serai analog is in `GeneratorPromotion::complete` (`crypto/dkg/promote/src/lib.rs:120-167`), which indexes an untrusted `HashMap<Participant, GeneratorProof>` with a stale/incorrect key scheme assumption: it validates only `proofs.len() == n - 1` and `i <= n`, but never rejects a proof keyed to the local participant `params.i()`. It then unconditionally `unwrap()`s `proofs.get(&i)` for every `i in 1..=n` other than itself. A map of the correct length that contains an entry keyed to `params.i()` (in place of some other participant's) passes all checks and then panics on `proofs.get(&i).unwrap()` for the missing participant — a host panic triggered entirely by attacker-supplied participant indices.

### Finding Description
`complete` performs two validations on the attacker-influenced `proofs` map:

- `proofs.len() != n - 1` → error (line 125)
- `u16::from(i) > params.n()` → error (line 133)

Both checks treat the map as an opaque set of `n - 1` keys in `1..=n`. They never verify the map is exactly `all_participants \ {params.i()}`. The loop at lines 140-156 then iterates `i in 1..=n`, skips `params.i()`, and calls `proofs.get(&i).unwrap()` (line 146). If the map contains a duplicate semantic entry — i.e., a proof filed under `params.i()` itself — then one legitimate participant `j` is absent, the length/count bounds still pass, and `proofs.get(&j).unwrap()` panics.

Because `Participant` keys are deserializable untrusted data (`Participant::read`/`ThresholdKeys::read` family) and the map is assembled from received promotion proofs, an unprivileged counterparty can supply a proof set `{(i_self, proof_a), (p2, proof_b), ...}` of length `n - 1` that omits some participant `j`. This mirrors the advisory exactly: an indexing assumption ("keys 1..=n minus self are present") is wrong after a refactoring of the indexing scheme, and the only reachable outcome is a host panic — a denial of service.

### Impact Explanation
A panic inside `complete` aborts key promotion for the entire validator set and, in a host that does not catch panics, crashes the process running the threshold-key infrastructure. Per the advisory's own severity model (availability-only, `A:H`, no confidentiality/integrity impact), this is a Medium denial-of-service analog.

### Likelihood Explanation
Any participant in a `GeneratorPromotion` session can produce this input: the caller merely needs the `proofs` map to contain a `Participant` key equal to the victim's own index (or to omit any participant while keeping length `n - 1`, e.g., by substituting a spurious-but-valid `Participant`). No collusion, no broken BFT assumptions, and no leaked keys are required — only control over the indices under which proofs are filed, which is attacker-influenced protocol input. The panic path is deterministic.

### Recommendation
Replace the length + upper-bound checks with an exact membership check before indexing:

```rust
// crypto/dkg/promote/src/lib.rs, in complete()
for i in self.base.params().all_participant_indexes() {
  if i == params.i() { continue; }
  if !proofs.contains_key(&i) {
    Err(PromotionError::InvalidParticipant { n: params.n(), participant: i })?;
  }
}
// also reject proofs.contains_key(&params.i())
```

and replace `proofs.get(&i).unwrap()` with a non-panicking lookup that returns `PromotionError`. The same `validate_map`-style check used by `PedPoP` (`dkg/pedpop/src/lib.rs:468`) and `frost::sign` (`frost/src/sign.rs:313`) should be applied here.

### Proof of Concept
```rust
// Victim is participant 1 of a n=3, t=2 key set.
// Attacker files proofs under {Participant(1), Participant(3)} — len 2 == n-1,
// all keys <= n, yet participant 2 is missing.
let mut proofs = HashMap::new();
proofs.insert(Participant::new(1).unwrap(), attacker_proof); // keyed to victim's own index
proofs.insert(Participant::new(3).unwrap(), p3_proof);
// proofs.len() == 2 == n - 1: passes line 125
// all keys <= 3: passes line 133
promotion.complete(&proofs);
// Loop: i=1 skipped; i=2 -> proofs.get(&Participant(2)) == None -> unwrap() panics
// at crypto/dkg/promote/src/lib.rs:146
```

Caveat: this conclusion relies on `proofs` map keys being attacker-influenceable (they are protocol-supplied `Participant` indices, not locally trusted constants) and on there being no upstream guarantee that the map excludes `params.i()`; no such guard exists in `complete` itself at `crypto/dkg/promote/src/lib.rs:124-146`.