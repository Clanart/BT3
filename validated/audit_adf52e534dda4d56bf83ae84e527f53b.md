### Title
Missing participant-key validation in `GeneratorPromotion::complete` causes an index-out-of-set panic (DoS) - ([File: crypto/dkg/promote/src/lib.rs])

### Summary
Analogous to CVE-2022-48619 — where an event code falling outside a bitmap was used without validation and caused a kernel panic — `GeneratorPromotion::complete` in `crypto/dkg/promote/src/lib.rs` validates only the *quantity* and *upper bound* of the participant indexes in the untrusted `proofs` map, then unconditionally indexes the map with `proofs.get(&i).unwrap()` for every expected participant. A proof map containing a well-formed but *wrong* index set (e.g., one entry keyed under the recipient's own participant index) passes validation and then panics when an expected participant's entry is absent.

### Finding Description
`complete` performs two checks on the caller-supplied `proofs: &HashMap<Participant, GeneratorProof<C1>>`:

1. `proofs.len() == n - 1` (line 125).
2. Every key `k` satisfies `k <= n` (lines 132–136).

Neither check ensures the map contains exactly the set `{1..=n} \ {params.i()}`. Specifically, the map may legitimately contain an entry keyed under `params.i()` — the recipient's own index, which is *not* expected in the map since the recipient inserts their own share separately at line 139.

The completion loop then iterates every `i` in `1 ..= n` (skipping `params.i()`) and does `proofs.get(&i).unwrap()` at line 146. Because a `HashMap` keyed under `params.i()` consumes one of the `n - 1` allowed slots, at least one required participant `j ∈ {1..=n} \ {params.i()}` is necessarily absent from the map, and `proofs.get(&j)` returns `None`, hitting `.unwrap()` and panicking.

The panic occurs *before* any DLEq proof verification, so the attacker does not need to produce a valid `GeneratorProof` for the displaced slot — any `GeneratorProof` (including a garbage one that would have failed `verify`) suffices to trigger the crash.

### Impact Explanation
Any participant (or party capable of submitting a `GeneratorProof` map for consumption by `complete`) can deterministically crash the node executing a generator-promotion round. This is a remote denial of service against the promotion/ceremony flow: the victim's process aborts inside `complete` with no error path, aborting the DKG-related operation and potentially killing the processor task handling it. Severity is Medium (availability only; no secret material or signature correctness is affected — the panic precedes verification, so no invalid share is ever accepted).

### Likelihood Explanation
Reachable whenever `GeneratorPromotion::complete` is invoked with a `proofs` map assembled from untrusted per-participant messages, which is the intended usage: proofs are broadcast by the other `n - 1` participants and collected into a map keyed by claimed participant index. The map keys are attacker-influenced `u16` indexes; the validation checks (`len == n - 1`, `key <= n`) are exactly the "falls within the bitmap's declared bound" check from the kernel bug — the code verifies the indexes are in-range individually but never verifies the *set membership* required by the subsequent unconditional indexing. One malformed message is sufficient; no threshold cooperation is needed.

### Recommendation
Before the completion loop, validate set membership rather than only count and bound. Either:
- Reject any `proofs` key equal to `params.i()` and confirm `proofs.keys()` equals `{1..=n} \ {params.i()}` exactly, or
- Replace `proofs.get(&i).unwrap()` at line 146 with a checked lookup returning `Err(PromotionError::IncorrectAmountOfParticipants { .. })` / a new `MissingParticipant(i)` variant.

### Proof of Concept
```rust
// C1: any in-scope Ciphersuite (e.g., Ristretto); C2 shares C1::F and C1::G.
// Victim has base keys with params (t = n, n = 3, i = 2).
let (promotion, _own_proof) =
    GeneratorPromotion::<C1, C2>::promote(&mut OsRng, base_keys_i2);

// Attacker-controlled map: n - 1 = 2 entries, every key <= n = 3,
// but one entry is keyed under the *victim's own* index (2).
let mut proofs = HashMap::new();
proofs.insert(Participant::new(2).unwrap(), attacker_proof_a); // key == params.i()
proofs.insert(Participant::new(3).unwrap(), attacker_proof_b); // participant 1 absent

// Passes: proofs.len() == n - 1; all keys <= n.
// Loop hits i = 1 -> proofs.get(&1) == None -> .unwrap() PANICS.
let _ = promotion.complete(&proofs);
```

Root cause: `proofs.get(&i).unwrap()` at `crypto/dkg/promote/src/lib.rs:146`, reachable because the only validation (lines 125–136) checks `proofs.len() == n - 1` and `key <= n` but never excludes `params.i()` nor requires presence of all `1..=n` participants.