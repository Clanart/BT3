### Title
DoS via out-of-range/missing participant index causing `unwrap()` panic on empty map entry during generator promotion - (File: crypto/dkg/promote/src/lib.rs)

### Summary
Analogous to CVE-2016-9448 — where setting a tag to a value accessing a 0-byte array caused a NULL dereference crash — `GeneratorPromotion::complete` indexes a participant map built from untrusted per-participant proof messages and calls `unwrap()` on `HashMap::get`, panicking when an expected participant entry is absent (i.e., accessing an empty/0-element slot). An unprivileged participant in the DKG/promotion protocol can trigger a remote crash of any node completing the promotion.

### Finding Description
`GeneratorPromotion::complete` validates only two things about the attacker-influenced `proofs` map before indexing it:

1. `proofs.len() == params.n() - 1` (crypto/dkg/promote/src/lib.rs:125)
2. every key satisfies `u16::from(i) <= params.n()` (crypto/dkg/promote/src/lib.rs:132-136)

It then iterates `for i in 1 ..= params.n()` skipping only `params.i()` and does:

```rust
let proof = proofs.get(&i).unwrap();
```

at crypto/dkg/promote/src/lib.rs:146.

A `HashMap<Participant, _>` cannot contain duplicate keys. If the map of `n - 1` distinct valid participants includes `params.i()` (the local node's own index — for example because a malicious participant replayed/spoofed a proof under the victim's index, or the caller keying by claimed sender was fed a crafted set), then some other participant `j` is necessarily absent from `1..=n`. When the loop reaches `j`, `proofs.get(&j)` returns `None` and `.unwrap()` panics. The length check cannot catch this because the map still holds `n - 1` entries; the bound check cannot catch it because all keys are `<= n`. There is no check that the key set equals `1..=n \ {params.i()}`.

### Impact Explanation
The panic unwinds through the promotion completion path, crashing the calling node/processor mid-protocol. This aborts key rotation (generator promotion is used to migrate threshold keys between generators, e.g., Bitcoin Taproot), denying availability of the signing group — the same availability impact class as CVE-2016-9448 (remote crash from a malformed tag/value). It is reachable purely from protocol messages a normal DKG participant can emit; no validator privileges, collusion, or leaked keys are required.

### Likelihood Explanation
Any participant able to submit `GeneratorProof` messages (all DKG members) can influence the proof map contents/keys. Reaching the panic only requires the collected map to be missing one expected index while retaining `n - 1` valid entries — a single omitted or mis-attributed proof suffices. The failure is deterministic once the malformed set is supplied.

### Recommendation
Replace the `unwrap()` at crypto/dkg/promote/src/lib.rs:146 with a checked lookup returning `PromotionError::MissingParticipant(i)` (or reuse `InvalidParticipant`), and/or explicitly verify the key set equals `1 ..= params.n()` minus `params.i()` before iterating. Additionally reject a proof keyed by `params.i()`.

### Proof of Concept
```rust
// Inside a test with ThresholdKeys over `Ristretto`, n = 5, victim i = 1
let (promotion, _own_proof) =
    GeneratorPromotion::<_, AltGenerator<Ristretto>>::promote(&mut OsRng, keys[0].clone());

// Build proofs for participants {1, 2, 3, 4} -- contains the victim's own index
// (e.g., replayed/spoofed by a malicious peer) and omits participant 5.
// proofs.len() == 4 == n - 1: both checks pass.
let mut proofs = HashMap::new();
for i in [1u16, 2, 3, 4] {
    let p = Participant::new(i).unwrap();
    let (_, proof) =
        GeneratorPromotion::<_, AltGenerator<Ristretto>>::promote(&mut OsRng, keys[usize::from(i - 1)].clone());
    proofs.insert(p, proof);
}

// promotion.complete(&proofs) -> panics at promote/src/lib.rs:146
// `proofs.get(&Participant(5)).unwrap()` on a missing entry.
promotion.complete(&proofs); // thread panics: called `Option::unwrap()` on a `None` value
``` [1](#0-0) 

Note: this is a denial-of-service (crash) analog matching the CVE-2016-9448 bug class of reaching an empty/absent slot via attacker-controlled selection. Whether a reachable panic on untrusted DKG input meets the engagement's impact bar should be weighed against its accepted-impact list; the panic itself is concretely reachable via the `proofs` map, which is populated from untrusted per-participant messages.

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L125-156)
```rust
    if proofs.len() != (usize::from(params.n()) - 1) {
      Err(PromotionError::IncorrectAmountOfParticipants {
        t: params.n(),
        n: params.n(),
        amount: proofs.len() + 1,
      })?;
    }
    for i in proofs.keys().copied() {
      if u16::from(i) > params.n() {
        Err(PromotionError::InvalidParticipant { n: params.n(), participant: i })?;
      }
    }

    let mut verification_shares = HashMap::new();
    verification_shares.insert(params.i(), self.proof.share);
    for i in 1 ..= params.n() {
      let i = Participant::new(i).unwrap();
      if i == params.i() {
        continue;
      }

      let proof = proofs.get(&i).unwrap();
      proof
        .proof
        .verify(
          &mut transcript(&self.base.original_group_key(), i),
          &[C1::generator(), C2::generator()],
          &[self.base.original_verification_share(i), proof.share],
        )
        .map_err(|_| PromotionError::InvalidProof(i))?;
      verification_shares.insert(i, proof.share);
    }
```
