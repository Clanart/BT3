### Title
`GeneratorPromotion::complete` accepts a proof map keyed to the local participant and panics on a missing peer, denying service — (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` validates the untrusted `proofs` map only by length (`proofs.len() == n - 1`) and per-key upper bound (`i <= n`), but never verifies the map actually contains every participant index `1..=n` other than `params.i()`. It then dereferences each expected entry with `proofs.get(&i).unwrap()`. A map of the correct size containing a proof keyed under the local participant's own index (or otherwise omitting any expected index while padding with a valid in-range key) passes both checks and causes a panic on the `unwrap` instead of a `PromotionError`.

### Finding Description
In `crypto/dkg/promote/src/lib.rs`, `complete` performs two validations on the caller/peer-supplied `HashMap<Participant, GeneratorProof<C1>>`:

- `proofs.len() != usize::from(params.n()) - 1` rejects wrong sizes (lines 125–131).
- Each key `i` is checked `u16::from(i) > params.n()` (lines 132–136).

Neither check ensures the set of keys equals `{1..=n} \ {params.i()}`. The subsequent loop iterates `1 ..= params.n()`, skips only `params.i()`, and indexes the map with `proofs.get(&i).unwrap()` (line 146). A `HashMap` deduplicates keys, so a map `{params.i(), j}` for `n = 3` (length 2 = n − 1, all keys ≤ n) omits participant `3`, whose `get` returns `None` and the `unwrap` panics.

This mirrors the reported bug class: an input measured against a size bound passes validation on account of a slot it doesn't actually occupy, and a subsequent indexing operation treated as unreachable panics on attacker-controlled data.

### Impact Explanation
The generator-promotion flow consumes `GeneratorProof`s received from other participants — untrusted inputs by design. A single participant (or any party able to inject an entry into the map an integrator builds from received proofs, e.g., by labeling their proof with a foreign participant index) can crash the completing party's process. In a validator context this aborts the promotion ceremony and can be repeated, preventing key migration to the new generator. Severity is Medium: a reachable panic/DoS on untrusted input, without secret leakage or incorrect acceptance.

### Likelihood Explanation
The trigger requires a `proofs` map of exactly `n − 1` entries containing a key equal to `params.i()` (or any key in `1..=n` that displaces a genuinely-needed participant). A malicious or misbehaving peer controlling one proof entry can induce this whenever the integrator keys the map by a peer-claimed index rather than a verified identity. No collusion, no threshold of participants, and no special timing is needed.

### Recommendation
Replace the length + bound checks with an exact-set check: verify `proofs.len() == n - 1` *and* that every expected `Participant` in `1..=n` except `params.i()` is present (e.g., iterate `1..=n` and `proofs.get(&i).ok_or(PromotionError::IncorrectAmountOfParticipants{..})?`, or explicitly reject `i == params.i()` keys). The `unwrap` at line 146 and the terminal `unwrap` at line 165 should be converted to error returns.

### Proof of Concept
```rust
// n = 3, local participant i = 1
let mut proofs = HashMap::new();
// A peer submits a proof keyed as participant 1 (the local index)
proofs.insert(Participant::new(1).unwrap(), foreign_proof);
proofs.insert(Participant::new(2).unwrap(), valid_proof_from_2);
// proofs.len() == 2 == n - 1; all keys <= 3. Both checks pass.
// Loop: i=1 skipped (self), i=2 ok, i=3 -> proofs.get(&3) == None -> panic!
promotion.complete(&proofs);
```