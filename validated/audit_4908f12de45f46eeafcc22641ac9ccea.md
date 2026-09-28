### Title
Panic on out-of-range/self participant proofs in `GeneratorPromotion::complete` enables remote denial of service - (File: crypto/dkg/promote/src/lib.rs)

### Summary
CVE-2020-2812 is an availability-only bug: attacker-reachable input crashes the MySQL server. The analog in Serai is an attacker-reachable panic in `GeneratorPromotion::complete` (`crypto/dkg/promote/src/lib.rs`). The function validates only the *count* and *upper bound* of the supplied `proofs` map, then iterates `1 ..= params.n()` and unconditionally `unwrap()`s a lookup for every expected participant. A peer who supplies a `proofs` map that omits any expected participant (e.g., by including the completer's own index, or any duplicate-occupied slot) causes a `None.unwrap()` panic, crashing the caller.

### Finding Description
`GeneratorPromotion::complete` takes `proofs: &HashMap<Participant, GeneratorProof<C1>>`, where each `GeneratorProof` is attacker-controlled data parsed via `GeneratorProof::read`. The validation is:

```rust
// crypto/dkg/promote/src/lib.rs:125-136
if proofs.len() != (usize::from(params.n()) - 1) {
  Err(PromotionError::IncorrectAmountOfParticipants { ... })?;
}
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() {
    Err(PromotionError::InvalidParticipant { ... })?;
  }
}
```

It then iterates expected participants and indexes the map:

```rust
// crypto/dkg/promote/src/lib.rs:140-147
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() {
    continue;
  }
  let proof = proofs.get(&i).unwrap();
```

Two gaps exist:

1. The map is never checked to *exclude* `params.i()` (the completer's own index). A `proofs` map of length `n - 1` containing the completer's own `Participant` necessarily omits some honest participant `j`. Since `j` is iterated and `j != params.i()`, `proofs.get(&j)` returns `None` and `.unwrap()` panics.
2. The bound check only rejects `i > n`; it does not verify the key set equals `{1..=n} \ {params.i()}`.

`Participant::new(i).unwrap()` inside the loop is safe (i ranges 1..=n), but `proofs.get(&i).unwrap()` is reachable with `None` purely from the shape of attacker-supplied keys.

### Impact Explanation
Promotion requires every participant to broadcast a `GeneratorProof` containing a DLEq proof of share equality across generators (`promote`, `crypto/dkg/promote/src/lib.rs:106-114`). Any participant (or any party able to inject a proof into the delivered map) can craft a well-formed `proofs` map of the correct cardinality and with all keys `<= n`, yet missing an expected participant — no invalid cryptography needed, since the panic fires before any proof is verified for the omitted slot. The panic aborts the promotion path and, in a runtime that does not isolate the task, crashes the process — the same "hang or frequently repeatable crash" availability impact class as the source advisory.

### Likelihood Explanation
Triggering requires only control over the `proofs` map contents — attacker-chosen `Participant` keys, not forged proofs. This is reachable by an unprivileged protocol peer delivering untrusted `GeneratorProof` messages to a `complete` call. No threshold collusion, leaked keys, or invalid curve points are required; the check order means the panic occurs deterministically whenever the key set is missing an expected member.

### Recommendation
Replace the unchecked `unwrap()` with a membership error, and enforce the exact expected key set. Concretely:

```rust
let proof = proofs
  .get(&i)
  .ok_or(PromotionError::InvalidParticipant { n: params.n(), participant: i })?;
```

and reject a `proofs` map containing `params.i()` (e.g., `if proofs.contains_key(&params.i()) { Err(...) }`), or build the expected set `{1..=n} \ {i}` and compare key sets directly. Alternatively reuse a `validate_map`-style helper as PedPoP/FROST do (`crypto/dkg/pedpop/src/lib.rs:57-83`, `crypto/frost/src/sign.rs:313`).

### Proof of Concept
Conceptual trigger, assuming `n = 5`, our index `params.i() = 1`:

1. Attacker delivers `proofs` with keys `{1, 2, 3, 4}` — length 4 = `n - 1`, all `<= n`, but includes self (`1`) and omits `5`. Proof payloads can be arbitrary bytes passing `GeneratorProof::read`.
2. `complete` passes both checks (`proofs.len() == 4`, all keys `<= 5`).
3. Loop: `i = 1` skipped (`== params.i()`); `i = 2, 3, 4` verified; `i = 5` → `proofs.get(&Participant(5))` is `None` → `.unwrap()` panics.
4. Result: crash before `PromotionError` can be returned, DoS-ing the promotion.

An equivalent variant: keys `{2, 3, 4, 4}` is impossible (HashMap dedups, lowering len), so the practical trigger is substituting any wrong `<= n` key — e.g., `{1, 2, 3, 4}` — which is exactly the self-inclusion gap.

Note: I verified the panic path and surrounding validation directly in `crypto/dkg/promote/src/lib.rs`; the corresponding self-exclusion check is present in the FROST `sign` path via `validate_map` but absent here.