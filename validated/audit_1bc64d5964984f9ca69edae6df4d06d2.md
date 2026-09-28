### Title
Empty DLEq statement list produces a universally valid proof - ([File: crypto/dleq/src/lib.rs](crypto/dleq/src/lib.rs))

### Summary
`DLEqProof::verify` accepts empty `generators` and `points` vectors, allowing a proof to verify without constraining the response scalar or proving any discrete-logarithm statement.

### Finding Description
`verify` only checks that `generators.len() == points.len()`; when both are zero, it performs no statement verification and compares the supplied challenge against a deterministic transcript challenge containing only the `dleq` domain separator. [1](#0-0)  The proving path likewise permits an empty generator slice, producing a proof whose `s` value is never used by the verifier. [2](#0-1) 

### Impact Explanation
An attacker who can cause a verifier to evaluate an attacker-supplied DLEq proof against zero statements can satisfy verification without demonstrating knowledge of any discrete logarithm. Because `s` is only incorporated when reconstructing a nonce for each statement, any value remains valid when the statement list is empty. [3](#0-2)  This constitutes a forged proof for callers that derive the statement list length from untrusted input and rely on `verify` to reject malformed or empty statements.

### Likelihood Explanation
The vulnerable condition is directly reachable through the public `DLEqProof::verify` API with empty slices. Exploitation requires an integrating protocol to permit the prover to influence the number or construction of verified statements; in that case, the attacker does not need a secret key, malformed encodings, or invalid curve points. [4](#0-3) 

### Recommendation
Reject empty statement lists in both `DLEqProof::prove` and `DLEqProof::verify`, in addition to the existing length-equality check. For example, return `DLEqError::InvalidProof` when `generators.is_empty()` or `points.is_empty()`.

### Proof of Concept

```rust
// crypto/dleq/src/lib.rs

let base_transcript = RecommendedTranscript::new(b"example-dleq-protocol");
let scalar = Zeroizing::new(G::Scalar::random(&mut rng));

let mut prover_transcript = base_transcript.clone();
let mut proof = DLEqProof::<G>::prove(
  &mut rng,
  &mut prover_transcript,
  &[],
  &scalar,
);

// The response scalar is never constrained because no statements are verified.
proof.s += G::Scalar::ONE;

let mut verifier_transcript = base_transcript;
assert!(proof.verify(&mut verifier_transcript, &[], &[]).is_ok());
```

### Citations

**File:** crypto/dleq/src/lib.rs (L123-141)
```rust
  pub fn prove<R: RngCore + CryptoRng, T: Transcript>(
    rng: &mut R,
    transcript: &mut T,
    generators: &[G],
    scalar: &Zeroizing<G::Scalar>,
  ) -> DLEqProof<G> {
    let r = Zeroizing::new(G::Scalar::random(rng));

    transcript.domain_separate(b"dleq");
    for generator in generators {
      // R, A
      Self::transcript(transcript, *generator, *generator * r.deref(), *generator * scalar.deref());
    }

    let c = challenge(transcript);
    // r + ca
    let s = (c * scalar.deref()) + r.deref();

    DLEqProof { c, s }
```

**File:** crypto/dleq/src/lib.rs (L146-157)
```rust
  fn verify_statement<T: Transcript>(
    transcript: &mut T,
    generator: G,
    point: G,
    c: G::Scalar,
    s: G::Scalar,
  ) {
    // s = r + ca
    // sG - cA = R
    // R, A
    Self::transcript(transcript, generator, (generator * s) - (point * c), point);
  }
```

**File:** crypto/dleq/src/lib.rs (L160-177)
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
```
