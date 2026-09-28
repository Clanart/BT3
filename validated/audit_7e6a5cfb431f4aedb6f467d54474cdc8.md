### Title
Forged DLEq proofs are accepted for empty statements - (File: crypto/dleq/src/lib.rs)

### Summary
**Severity:** Medium

`DLEqProof::verify` accepts an empty generator/point list. An unprivileged party can therefore submit a proof that verifies no discrete-log equality statements at all, similar to submitting a proof against an uninitialized or non-existent Merkle root.

### Finding Description

`DLEqProof::verify` checks only that `generators.len() == points.len()`. When both slices are empty, the verification loop performs zero iterations and the verifier derives a deterministic challenge from a transcript containing only the `dleq` domain separator. The proof’s `s` value is never used because there is no statement to evaluate. [1](#0-0) 

`DLEqProof::read` accepts any canonical `c` and `s` scalars without requiring either to be non-zero or requiring the proof to correspond to a non-empty statement. [2](#0-1) [3](#0-2) 

Consequently, an attacker can calculate the expected challenge from the public transcript and submit `(c, s)` where `s` is arbitrary, including zero. Verification succeeds despite proving nothing.

### Impact Explanation

Any protocol that dynamically supplies the generator and point lists to `DLEqProof::verify` can accept a forged proof when that list is empty or uninitialized. This converts a missing verification subject into successful verification rather than rejection, allowing an attacker to claim proof of a non-existent discrete-log equality statement.

The in-repo PedPoP usage supplies two fixed statements, so that particular path is not affected. [4](#0-3)  The vulnerability exists in the public `DLEqProof` verification API where an untrusted proof can be deserialized and verified against an empty statement.

### Likelihood Explanation

Exploitation is deterministic whenever the attacker can cause verification with empty `generators` and `points` slices. No discrete-log knowledge, valid witness, or secret material is required. The likelihood depends on a caller accepting an empty statement list, but the API performs no defense against this edge case.

### Recommendation

Reject empty proof statements in `DLEqProof::verify`:

```rust
pub fn verify<T: Transcript>(
  &self,
  transcript: &mut T,
  generators: &[G],
  points: &[G],
) -> Result<(), DLEqError> {
  if generators.is_empty() || (generators.len() != points.len()) {
    Err(DLEqError::InvalidProof)?;
  }

  transcript.domain_separate(b"dleq");
  for (generator, point) in generators.iter().zip(points) {
    Self::verify_statement(transcript, *generator, *point, self.c, self.s);
  }

  if self.c != challenge(transcript) {
    Err(DLEqError::InvalidProof)?;
  }

  Ok(())
}
```

This ensures that a successful verification always corresponds to at least one concrete discrete-log equality statement.

### Proof of Concept

The following crate-internal test demonstrates that a proof with an arbitrary `s` succeeds when no statements are supplied:

```rust
// crypto/dleq/src/tests/mod.rs
#[test]
fn empty_statement_forgery() {
  use transcript::RecommendedTranscript;
  use k256::{ProjectivePoint, Scalar};
  use group::ff::Field;

  let mut transcript = RecommendedTranscript::new(b"DLEq Proof Test");
  transcript.domain_separate(b"dleq");
  let c = crate::challenge::<_, Scalar>(&mut transcript);

  let proof = crate::DLEqProof::<ProjectivePoint> {
    c,
    s: Scalar::ZERO,
  };

  assert!(proof
    .verify(&mut RecommendedTranscript::new(b"DLEq Proof Test"), &[], &[])
    .is_ok());
}
```

The empty `&[]` arguments pass the length check, skip every verification statement, and leave only the publicly computable challenge check. [5](#0-4)

### Citations

**File:** crypto/dleq/src/lib.rs (L87-97)
```rust
// Helper function to read a scalar
#[cfg(feature = "serialize")]
fn read_scalar<R: Read, F: PrimeField>(r: &mut R) -> io::Result<F> {
  let mut repr = F::Repr::default();
  r.read_exact(repr.as_mut())?;
  let scalar = F::from_repr(repr);
  if scalar.is_none().into() {
    Err(Error::other("invalid scalar"))?;
  }
  Ok(scalar.unwrap())
}
```

**File:** crypto/dleq/src/lib.rs (L160-179)
```rust
  pub fn verify<T: Transcript>(
    &self,
    transcript: &mut T,
    generators: &[G],
    points: &[G],
  ) -> Result<(), DLEqError> {
    if generators.len() != points.len() {
      Err(DLEqError::InvalidProof)?;
    }

    transcript.domain_separate(b"dleq");
    for (generator, point) in generators.iter().zip(points) {
      Self::verify_statement(transcript, *generator, *point, self.c, self.s);
    }

    if self.c != challenge(transcript) {
      Err(DLEqError::InvalidProof)?;
    }

    Ok(())
```

**File:** crypto/dleq/src/lib.rs (L189-193)
```rust
  /// Read a DLEq proof from something implementing Read.
  #[cfg(feature = "serialize")]
  pub fn read<R: Read>(r: &mut R) -> io::Result<DLEqProof<G>> {
    Ok(DLEqProof { c: read_scalar(r)?, s: read_scalar(r)? })
  }
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
