### Title
Empty aggregate Schnorr signature verifies successfully - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts an aggregate containing zero nonces and `SchnorrAggregate::verify` treats the resulting empty statement list as valid when `s == 0`. [1](#0-0) [2](#0-1) 

### Finding Description
The deserializer reads a caller-controlled `u32` nonce count and accepts `0` without requiring at least one aggregate signature. [3](#0-2)  It then accepts the canonical zero scalar as `s`. [4](#0-3) [5](#0-4) 

During verification, the only rejection condition is a length mismatch between `Rs` and `keys_and_challenges`. [6](#0-5)  If both are empty, the verifier appends only `(-0, generator)` to the multiexp and checks whether the result is identity, which always succeeds. [7](#0-6) 

### Impact Explanation
An unauthenticated caller can supply an encoding for an aggregate signature with no signers and have it accepted by `SchnorrAggregate::verify` whenever the expected signer/challenge list is empty. [1](#0-0) [2](#0-1)  In a caller that derives the expected statement list solely from attacker-supplied or otherwise empty state, this permits a forged aggregate signature to authorize an action without any private key. [8](#0-7) 

### Likelihood Explanation
The malformed encoding is deterministic and trivial to construct, requiring only a zero length and a canonical zero scalar. [1](#0-0)  Exploitation requires the verifier to be invoked with an empty `keys_and_challenges` slice, so the practical risk depends on whether an upstream protocol can reach this API with an empty signer set. [2](#0-1) 

### Recommendation
Reject empty aggregate signatures during `SchnorrAggregate::read` or at the start of `SchnorrAggregate::verify`. [1](#0-0) [6](#0-5)  Requiring `!self.Rs.is_empty()` and `!keys_and_challenges.is_empty()` prevents vacuous verification. [2](#0-1) 

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use schnorr::aggregate::SchnorrAggregate;

// For a ciphersuite whose scalar encoding is 32 bytes, this encodes:
// u32_le(0) || scalar_zero.
let mut encoded = Vec::new();
encoded.extend(0u32.to_le_bytes());
encoded.extend([0u8; 32]);

let aggregate =
  SchnorrAggregate::<Ristretto>::read(&mut encoded.as_slice()).unwrap();

assert_eq!(aggregate.Rs().len(), 0);

// Accepted because the equation contains only (-0) * G == identity.
assert!(aggregate.verify(b"application dst", &[]));
```

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L118-145)
```rust
  /// Perform signature verification.
  ///
  /// Challenges must be properly crafted, which means being binding to the public key, nonce, and
  /// any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  ///
  /// The DST used here must prevent a collision with whatever hash function produced the
  /// challenges.
  #[must_use]
  pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
    if self.Rs.len() != keys_and_challenges.len() {
      return false;
    }

    let mut digest = DigestTranscript::<C::H>::new(dst);
    digest.domain_separate(b"signatures");
    for (_, challenge) in keys_and_challenges {
      digest.append_message(b"challenge", challenge.to_repr());
    }

    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```

**File:** crypto/ciphersuite/src/lib.rs (L74-82)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
```
