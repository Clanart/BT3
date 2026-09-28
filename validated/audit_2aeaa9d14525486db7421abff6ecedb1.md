### Title
Malformed generator-promotion proof set can crash completion via missing-participant unwrap - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` trusts the cardinality and upper bound of a caller-provided proof map, but does not ensure every required participant is present. A participant can submit a proof keyed by the local participant index instead of the missing remote participant; validation passes, then `proofs.get(&i).unwrap()` panics when the missing participant is reached. [1](#0-0) 

### Finding Description
The function checks only `proofs.len() == n - 1` and that every key is at most `n`. [2](#0-1)  It does not reject `i == params.i()`, nor does it verify that the map contains each participant other than the local participant. [3](#0-2)  The subsequent iteration skips only the local index and unconditionally unwraps every other participant’s proof. [4](#0-3)  `GeneratorProof::read` accepts attacker-controlled bytes for the promoted share and proof, while the `Participant` map key is selected by the supplying party/caller-side aggregation. [5](#0-4) 

### Impact Explanation
An unprivileged participant can cause a deterministic panic during generator promotion without supplying a cryptographically valid proof. [4](#0-3)  The panic occurs before the omitted participant’s proof is read or verified, so the attacker does not need a valid `DLEqProof`. [6](#0-5)  If promotion completion runs in a service or validator process that does not isolate panics per message, this produces a denial of service analogous to the crafted-offset crash class.

### Likelihood Explanation
The attack requires only control over one participant key in the proof map and omission of another required participant. [7](#0-6)  For `n = 2`, a map containing only `params.i()` has the expected length `n - 1`, passes the `<= n` check, and deterministically reaches an absent entry for the other participant. [8](#0-7)  The exploit depends on integrators accepting externally supplied participant-to-proof mappings without independently enforcing uniqueness against the complete participant set.

### Recommendation
Validate exact membership before indexing: reject any proof keyed by `params.i()`, and require `proofs` to contain every `Participant` in `1..=n` except `params.i()`. [7](#0-6)  Replace `proofs.get(&i).unwrap()` with an error-returning lookup such as `ok_or(PromotionError::IncorrectAmountOfParticipants)` or a dedicated missing-participant error. [9](#0-8) 

### Proof of Concept
For `n = 2` and local participant `i = 1`, provide a map containing only participant `1` and omit participant `2`. The cardinality check accepts `1 == 2 - 1`, the upper-bound check accepts `1 <= 2`, and the loop later evaluates `proofs.get(&Participant(2)).unwrap()`, panicking before proof verification.

```rust
// crypto/dkg/promote/src/lib.rs
let params = promotion.base.params(); // t = 2, n = 2, i = Participant(1)
let attacker_proof: GeneratorProof<C1> = /* any bytes accepted by GeneratorProof::read */;

let mut proofs = HashMap::new();
// Malformed binding: the attacker supplies the local participant index
// instead of the required remote participant index.
proofs.insert(params.i(), attacker_proof);

// Passes quantity/bounds checks, then panics on `proofs.get(&Participant(2)).unwrap()`.
let _ = promotion.complete(&proofs);
```

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L36-49)
```rust
  /// An incorrect amount of participants was specified.
  #[error("incorrect amount of participants. {t} <= amount <= {n}, yet amount is {amount}")]
  IncorrectAmountOfParticipants {
    /// The threshold required.
    t: u16,
    /// The total amount of participants.
    n: u16,
    /// The amount of participants specified.
    amount: usize,
  },

  /// Participant provided an invalid proof.
  #[error("invalid proof {0}")]
  InvalidProof(Participant),
```

**File:** crypto/dkg/promote/src/lib.rs (L61-77)
```rust
pub struct GeneratorProof<C: Ciphersuite> {
  share: C::G,
  proof: DLEqProof<C::G>,
}

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
  }
```

**File:** crypto/dkg/promote/src/lib.rs (L120-154)
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
      proof
        .proof
        .verify(
          &mut transcript(&self.base.original_group_key(), i),
          &[C1::generator(), C2::generator()],
          &[self.base.original_verification_share(i), proof.share],
        )
        .map_err(|_| PromotionError::InvalidProof(i))?;
```
