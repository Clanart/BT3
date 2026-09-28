### Title
Malformed generator-promotion proof roster triggers a panic - ([File: crypto/dkg/promote/src/lib.rs])

### Summary
`GeneratorPromotion::complete` accepts a caller-populated map of participant proofs and panics when the map contains a valid participant entry for the local participant but omits another participant. [1](#0-0) 

### Finding Description
`complete` only checks that `proofs.len() == n - 1` and that every participant key is at most `n`; it does not verify that the keys are exactly `1..=n` excluding `params.i()` or that `params.i()` is absent. [2](#0-1)  It then iterates every participant other than itself and unconditionally unwraps `proofs.get(&i)`. [3](#0-2)  Because `Participant::new` rejects only zero, a proof submitted under the victim’s own participant index still satisfies the explicit checks. [4](#0-3)  `GeneratorProof::read` exposes the peer-controlled object used in this map and parses an arbitrary encoded share and DLEq proof before any participant-set consistency check. [5](#0-4) 

### Impact Explanation
For `n = 2`, a proof set containing only the victim’s participant index has length `n - 1` and passes all explicit validation, but the loop unwraps the missing proof for the other participant and aborts through a panic. This provides an input-triggered denial of service against generator-promotion completion.

### Likelihood Explanation
An unprivileged counterparty only needs to cause a syntactically valid `GeneratorProof` to be associated with an incorrect participant ID while ensuring the map reaches the expected cardinality. The panic occurs before DLEq verification, so the submitted proof does not need to be cryptographically valid for the missing participant. [6](#0-5) 

### Recommendation
Validate that `proofs.keys()` is exactly `all_participant_indexes() − {params.i()}`, and explicitly reject `params.i()` before processing. Replace `proofs.get(&i).unwrap()` with a checked lookup returning `PromotionError::IncorrectAmountOfParticipants` or a dedicated missing-proof error.

### Proof of Concept
```rust
use std::collections::HashMap;
use dkg::{Participant, ThresholdParams};
use promote::{GeneratorProof, GeneratorPromotion};

// For any supported ciphersuites C1/C2 where:
//   base.params() == ThresholdParams::new(2, 2, Participant::new(1).unwrap())
let (promotion, own_proof) =
  GeneratorPromotion::<C1, C2>::promote(&mut rng, base);

// Simulate receiving serialized proof bytes under the wrong participant ID.
let encoded = own_proof.serialize();
let replayed = GeneratorProof::<C1>::read(&mut encoded.as_slice()).unwrap();

let mut proofs = HashMap::new();
proofs.insert(Participant::new(1).unwrap(), replayed);

// proofs.len() == n - 1 and participant 1 <= n, so explicit checks pass.
// The loop then calls proofs.get(&Participant::new(2)).unwrap() and panics.
let _ = promotion.complete(&proofs);
```

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L66-76)
```rust
impl<C: Ciphersuite> GeneratorProof<C> {
  pub fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.share.to_bytes().as_ref())?;
    self.proof.write(writer)
  }

  pub fn read<R: Read>(reader: &mut R) -> io::Result<GeneratorProof<C>> {
    Ok(GeneratorProof {
      share: <C as Ciphersuite>::read_G(reader)?,
      proof: DLEqProof::read(reader)?,
    })
```

**File:** crypto/dkg/promote/src/lib.rs (L119-154)
```rust
  /// Complete promotion by taking in the proofs from all other participants.
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
      proof
        .proof
        .verify(
          &mut transcript(&self.base.original_group_key(), i),
          &[C1::generator(), C2::generator()],
          &[self.base.original_verification_share(i), proof.share],
        )
        .map_err(|_| PromotionError::InvalidProof(i))?;
```

**File:** crypto/dkg/src/lib.rs (L30-35)
```rust
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```
