### Title
Missing-entry panic in `GeneratorPromotion::complete` when the proofs map contains the caller's own index instead of a required participant's - (File: crypto/dkg/promote/src/lib.rs)

### Summary
The upstream bug (CVE-2025-39903) is an "uninitialized node" flaw: `numa_nodes_parsed` was only populated for CPU-bearing nodes, so memory-only nodes were never marked, and a later consumer dereferenced the missing node and panicked. The Serai analog is in `GeneratorPromotion::complete`, which validates the `proofs` map only by its length (`n - 1`) and a maximum-index bound, then iterates `1 ..= n` skipping `params.i()` and unconditionally `unwrap`s `proofs.get(&i)`. A proofs map that contains the victim's own participant index — or any duplicate-of-self — passes both checks while omitting one required participant, so `proofs.get(&i)` returns `None` and the `.unwrap()` panics, exactly mirroring the uninitialized-node dereference.

### Finding Description
In `crypto/dkg/promote/src/lib.rs:124-146`, `complete` performs:

```rust
if proofs.len() != (usize::from(params.n()) - 1) { ... }
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() { ... }
}
...
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() { continue; }
  let proof = proofs.get(&i).unwrap();
```

The validation checks cardinality and an upper bound, but never checks that the map does not contain `params.i()` nor that every required participant `1..=n` (except self) is present. If a supplying participant provides a map keyed with the victim's own `Participant` index (or otherwise substitutes one required key), the length and bound checks pass while one `i` in `1..=n, i != params.i()` is absent. `proofs.get(&i).unwrap()` then panics — the same shape as `free_area_init()` dereferencing `NODE_DATA()` for a node never marked in `numa_nodes_parsed`: a presence set is populated for the wrong subset, and a later consumer assumes full coverage.

Reachability: `proofs` is a `HashMap<Participant, GeneratorProof<C1>>` of messages received from other participants, and each `GeneratorProof` is deserialized from untrusted bytes via `GeneratorProof::read` (used in `crypto/dkg/promote/src/tests.rs:90`). An unprivileged participant controls the `Participant` key under which its proof is delivered to the victim's `complete` call, so this path is reachable purely from attacker-chosen input to a `complete` API. No collusion or trusted-role assumption is needed.

### Impact Explanation
A single participant (or anyone able to deliver a malformed proof map for a session) crashes every honest validator running generator promotion — e.g., during Serai's key rotation where `ThresholdKeys` are promoted between generators. The panic aborts the promotion before `ThresholdKeys::new` is reached, denying availability of the rotated key set. This matches the CVE's Medium profile: local/unprivileged trigger, no confidentiality or integrity impact, high availability impact.

### Likelihood Explanation
Triggering requires only that a participant's proof be keyed under the victim's own index (or that any required index be omitted while length/bound checks still pass — trivially satisfiable since `n - 1` entries including self always has the right count). The missing-check is deterministic: the length check counts self, and the bound check passes since `params.i() <= n`. Any session where a faulty or adversarial participant submits such a map panics all completing nodes.

### Recommendation
In `GeneratorPromotion::complete` (`crypto/dkg/promote/src/lib.rs:132-136`), additionally reject `proofs` maps that contain `params.i()`, and verify the key set equals exactly `{1..=n} \ {params.i()}` — or replace `proofs.get(&i).unwrap()` with `.ok_or(PromotionError::MissingParticipant(i))?` — mirroring the upstream fix which marks memory nodes in `of_numa_parse_memory_nodes` so no uninitialized node is later dereferenced.

### Proof of Concept
Setup: a valid `ThresholdKeys<C>` set with `n = 5`, `t = 5`, and victim index `params.i() = 2` (per `crypto/dkg/promote/src/tests.rs:50-78`). Honest participants 1,3,4,5 produce `GeneratorProof`s. The attacker (participant 1) delivers its map to the victim as `{2: proof_1, 3: proof_3, 4: proof_4, 5: proof_5}` — i.e., keyed under the victim's index `2` instead of `1`.

Execution of `complete` on the victim:
- `proofs.len() == 4 == n - 1` → length check passes.
- All keys `{2,3,4,5} <= n` → bound check passes.
- Loop `i in 1..=5`, skipping `i == 2`: at `i == 1`, `proofs.get(&Participant(1))` returns `None` → `.unwrap()` panics at `crypto/dkg/promote/src/lib.rs:146`.

Result: panic (index-into-missing-node analog of the upstream NULL `NODE_DATA()` dereference), aborting promotion for the victim with attacker-controlled input only.