### Title
`GeneratorPromotion::complete` panics on a crafted participant set in `proofs`, causing a denial of service - (File: crypto/dkg/pedpop/../promote/src/lib.rs → crypto/dkg/promote/src/lib.rs)

### Summary
Analogous to CVE-2024-30917 (a crafted QoS `history_depth` parameter causing a crash/DoS), `GeneratorPromotion::complete` accepts an attacker-controlled `HashMap<Participant, GeneratorProof<C1>>` and validates only its *length* (`proofs.len() == n - 1`) and that every key is `<= n`. It never checks that every required participant index `1..=n` (minus `params.i()`) is actually present. A participant can supply `n - 1` proofs keyed by valid-but-wrong participants (e.g. including `params.i()` itself, which is not excluded), causing `proofs.get(&i).unwrap()` to panic on the first missing index — crashing the node performing generator promotion. [1](#0-0) 

### Finding Description
`complete` performs two checks before iterating:

1. `proofs.len() != n - 1` → `IncorrectAmountOfParticipants` (line 125).
2. `for i in proofs.keys()`: `u16::from(i) > params.n()` → `InvalidParticipant` (lines 132–136).

It then iterates `for i in 1 ..= params.n()`, skips `params.i()`, and unconditionally does `let proof = proofs.get(&i).unwrap();` (line 146). [2](#0-1) 

Because `HashMap` keys are unique and the only key-validity check is `i <= n`, a malicious participant can submit a map containing `params.i()` (the local node, which is never rejected) plus `n - 2` other valid indexes, omitting some required participant `j`. The length and bound checks both pass, and the loop hits `proofs.get(&j).unwrap()` → panic. The same crash occurs for any omitted index — the required-key set is never verified. Contrast with `dkg`'s `validate_map`/`ThresholdView::view`, which explicitly check membership and duplicates before indexing (`verification_shares[i]`, `self.commitments[&l]`). [3](#0-2) [4](#0-3) 

A closely related instance exists in PedPoP blame handling: `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` (panic if `decryptor` was never registered) and `blame_internal` indexes `self.commitments[&sender]` — both reachable with caller-supplied `Participant` values. [5](#0-4) [6](#0-5) 

### Impact Explanation
An unprivileged participant in the generator-promotion ceremony (anyone able to publish a `GeneratorProof` message) can crash any honest participant calling `complete` — a remote denial of service on a DKG-adjacent path, matching the CVE's "crafted parameter → crash" class (CVSS 5.5, local attacker → DoS; here reachable over the protocol's public message flow). Because the panic unwinds through `complete`, it aborts key promotion; if promotion runs inside a larger node process (as in Serai's processor), this is a full node crash, giving griefing/availability impact on threshold key rotation.

### Likelihood Explanation
High reachability: `proofs` is a map of per-participant messages naturally attacker-influenced in a distributed protocol, `complete` is a listed in-scope `complete` API, and the malicious map trivially satisfies both existing checks (right count, all keys `<= n`, just the wrong keys). No cryptographic forgery is needed — the panic triggers before any proof is verified. The only mitigation is that a panic produces an abort rather than silently wrong keys, hence Medium rather than High.

### Recommendation
Replace `proofs.get(&i).unwrap()` with a membership check returning `PromotionError`, e.g.:

```rust
let proof = proofs
  .get(&i)
  .ok_or(PromotionError::MissingParticipant(i))?;
```

and additionally reject `proofs` maps that contain `params.i()` (a proof from self is meaningless — the local share comes from `self.proof`). Apply the same fix pattern in `Decryption::decrypt_with_proof` (`enc_keys.get(&decryptor)`) and `blame_internal` (`commitments.get(&sender)`), returning an error/blame result instead of panicking on unregistered participants.

### Proof of Concept
```rust
// Setup: params with t == n == 3, local participant i = 1.
let proofs: HashMap<Participant, GeneratorProof<C>> = /* attacker-supplied map */;

// Attacker (participant 2) broadcasts proofs keyed { Participant(1), Participant(2) }
// i.e. includes the local node's index and omits participant 3.
// proofs.len() == 2 == n - 1   -> passes IncorrectAmountOfParticipants
// all keys <= 3                -> passes InvalidParticipant check

// In GeneratorPromotion::complete:
//   for i in 1..=3:
//     i == 1: skipped (params.i())
//     i == 2: proofs.get(&2) -> Some, verified
//     i == 3: proofs.get(&3).unwrap() -> panic!("called Option::unwrap() on a None value")
promotion.complete(&proofs); // thread panics -> node DoS
```

The panic occurs at `crypto/dkg/promote/src/lib.rs:146` (`proofs.get(&i).unwrap()`), before any cryptographic verification of the omitted participant's data, so no valid proof material is needed from the attacker.

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L120-146)
```rust
  pub fn complete(
    self,
    proofs: &HashMap<Participant, GeneratorProof<C1>>,
  ) -> Result<ThresholdKeys<C2>, PromotionError> {
    let params = self.base.params();
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
```

**File:** crypto/dkg/src/lib.rs (L463-491)
```rust
  pub fn view(&self, mut included: Vec<Participant>) -> Result<ThresholdView<C>, DkgError> {
    if (included.len() < self.params().t.into()) ||
      (usize::from(self.params().n()) < included.len())
    {
      Err(DkgError::IncorrectAmountOfParticipants {
        t: self.params().t,
        n: self.params().n,
        amount: included.len(),
      })?;
    }
    included.sort();
    {
      let mut found = included[0] == self.params().i();
      for i in 1 .. included.len() {
        if included[i - 1] == included[i] {
          Err(DkgError::DuplicatedParticipant(included[i]))?;
        }
        found |= included[i] == self.params().i();
      }
      if !found {
        Err(DkgError::NotParticipating)?;
      }
    }
    {
      let last = *included.last().unwrap();
      if u16::from(last) > self.params().n() {
        Err(DkgError::InvalidParticipant { n: self.params().n(), participant: last })?;
      }
    }
```

**File:** crypto/frost/src/lib.rs (L50-72)
```rust
pub fn validate_map<T>(
  map: &HashMap<Participant, T>,
  included: &[Participant],
  ours: Participant,
) -> Result<(), FrostError> {
  if (map.len() + 1) != included.len() {
    Err(FrostError::InvalidParticipantQuantity(included.len(), map.len() + 1))?;
  }

  for included in included {
    if *included == ours {
      if map.contains_key(included) {
        Err(FrostError::DuplicatedParticipant(*included))?;
      }
      continue;
    }

    if !map.contains_key(included) {
      Err(FrostError::MissingParticipant(*included))?;
    }
  }

  Ok(())
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-390)
```rust
    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;
```

**File:** crypto/dkg/pedpop/src/lib.rs (L596-604)
```rust
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
```
