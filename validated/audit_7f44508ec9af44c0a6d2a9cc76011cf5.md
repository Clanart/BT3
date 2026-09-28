### Title
Panic on missing `GeneratorProof` in `GeneratorPromotion::complete` enables remote DoS abort - ([File: crypto/dkg/promote/src/lib.rs](crypto/dkg/promote/src/lib.rs))

### Summary
Analogous to CVE-2016-9816 — a crash/abort triggered by externally supplied input reaching an unchecked path — `GeneratorPromotion::complete` panics on a `HashMap::get().unwrap()` when a participant submits a proof set that satisfies the count and index-range checks but omits a required participant (e.g., by including an entry keyed to the victim's own index). An unprivileged counterparty in a key-promotion session can remotely abort the victim process.

### Finding Description
`GeneratorPromotion::complete` validates the `proofs` map with two checks only:

1. `proofs.len() == n - 1` (line 125)
2. every key `i` satisfies `u16::from(i) <= params.n()` (lines 132-136)

It then iterates every participant `1 ..= n`, skipping `params.i()` (the local index), and unconditionally unwraps:

```rust
let proof = proofs.get(&i).unwrap();   // crypto/dkg/promote/src/lib.rs:146
```

There is no check that the map does *not* contain `params.i()` itself, and no check that every required participant is present. A peer can send `n - 1` proofs where one entry is keyed under the victim's own `Participant` index (or any valid-but-not-required index) and a required participant `j` is missing. Both checks pass: the length is `n - 1`, and `params.i() <= n`. When the loop reaches the missing `j`, `proofs.get(&j).unwrap()` panics, unwinding/aborting the host — the same "guest-triggered abort at elevated privilege" shape as the Xen async-abort crash.

By contrast, sibling code handles this correctly: `validate_map` in `crypto/frost/src/lib.rs:50` explicitly detects duplicated/missing participants, and `dkg`'s `view()` returns `DkgError::DuplicatedParticipant`/`NotParticipating` (`crypto/dkg/src/lib.rs:463-485`). `complete` uses a raw `HashMap` with none of that validation.

### Impact Explanation
A single malicious DKG/promotion participant causes a deterministic panic (process abort) on every node that calls `complete` with the attacker-influenced proof set. Since the panic happens before `ThresholdKeys::new`, the promotion session cannot complete, and in the Serai processor/coordinator context a panic in message handling can take down the validator's key-management task — a remote denial of service from untrusted network input. Severity: Medium (availability only; no secret leakage, matching the CVSS 6.5 availability-impact class of the reference CVE).

### Likelihood Explanation
Any participant in a generator-promotion session controls the `HashMap<Participant, GeneratorProof>` entries it delivers to peers (proofs are parsed from untrusted bytes via `GeneratorProof::read` at `crypto/dkg/promote/src/lib.rs:72`). Constructing the malicious map requires no valid proofs — the attacker only needs to control which `Participant` keys are sent; the omitted `j` never gets proof-verified because the panic precedes verification. Reachability requires only that the promotion protocol run, which occurs whenever keys are retargeted to a new generator.

### Recommendation
Replace the length/index checks plus `unwrap` with explicit set validation mirroring `frost::validate_map`: reject any key equal to `params.i()`, reject keys `> n`, and require every participant in `1 ..= n` except `params.i()` to be present. Return `PromotionError::IncorrectAmountOfParticipants`/`InvalidParticipant` instead of panicking. Alternatively, look up with `proofs.get(&i).ok_or(PromotionError::InvalidProof(i))?`.

### Proof of Concept
```rust
// Params: t = 2, n = 3, local i = 1
let params = ThresholdParams::new(2, 3, Participant::new(1).unwrap()).unwrap();
// ... obtain `base: ThresholdKeys<C1>` and run `promote`
let (promotion, _our_proof) = GeneratorPromotion::<C1, C2>::promote(&mut rng, base);

// Attacker-controlled map: n - 1 = 2 entries, but keyed to {1 (victim's i), 3},
// omitting participant 2. Both validation checks pass:
//   len == 2 == n-1; all keys <= n.
let mut proofs: HashMap<Participant, GeneratorProof<C1>> = HashMap::new();
proofs.insert(Participant::new(1).unwrap(), attacker_proof_a);
proofs.insert(Participant::new(3).unwrap(), attacker_proof_b);

// Panics at `proofs.get(&Participant::new(2)).unwrap()` — crypto/dkg/promote/src/lib.rs:146
let _ = promotion.complete(&proofs);
```