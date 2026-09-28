### Title
Empty aggregate signature is accepted for an empty signer set - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::read` accepts an aggregate containing zero nonce commitments, while `SchnorrAggregate::verify` treats zero `Rs` paired with zero keys/challenges and `s = 0` as a valid signature because its verifier equation reduces to `0G = 0`. The encoding path therefore accepts a signature that `SchnorrAggregator::complete` explicitly refuses to produce. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`SchnorrAggregate::read` reads a four-byte count, permits that count to be zero, and then reads only the scalar `s`. [1](#0-0)  During verification, the function only checks `self.Rs.len() == keys_and_challenges.len()`; for two empty slices this check succeeds, the loop adds no key-bound terms, and the sole remaining term is `(-s)G`. [2](#0-1)  Supplying `s = 0` therefore makes the multiexp identity and causes `verify` to return `true` without any signer, nonce, challenge, public key, or discrete-logarithm relationship. [4](#0-3) 

This is inconsistent with the generation API: `SchnorrAggregator::complete` returns `None` when no signatures were aggregated rather than emitting an empty aggregate. [5](#0-4)  The deserialization/verification path nevertheless creates a second accepted representation for an unsigned aggregate. [1](#0-0) 

### Impact Explanation
An unprivileged party can submit the 36-byte encoding `u32_le(0) || F::ZERO` wherever untrusted bytes reach `SchnorrAggregate::read` and are then checked with an empty signer/key list. If an application or consensus layer interprets `verify(...) == true` as cryptographic authorization without independently requiring a non-empty authorized signer set or threshold, the forged aggregate satisfies the signature predicate with no private key and no participating signer. This is a deterministic forged signature rather than a probabilistic proof error. [1](#0-0) [2](#0-1) 

### Likelihood Explanation
The attack requires no signer compromise, malformed curve points, non-canonical encodings, repeated nonces, or protocol interaction. The only prerequisite is a reachable caller that invokes aggregate verification with an empty `keys_and_challenges`/signer list; the cryptographic crate itself neither rejects that configuration nor enforces the non-empty invariant already enforced by its own aggregator. [2](#0-1) [5](#0-4) 

### Recommendation
Reject empty aggregates in the verifier and preferably at the trust boundary in `SchnorrAggregate::read`, matching `SchnorrAggregator::complete`’s non-empty invariant. At minimum, `verify` should return `false` when `self.Rs.is_empty()` or `keys_and_challenges.is_empty()` before evaluating the multiexp, and callers should additionally enforce the required signer threshold as a distinct policy check. [2](#0-1) [5](#0-4) 

### Proof of Concept
```rust
// crypto/schnorr/src/aggregate.rs
use ciphersuite::{group::ff::Field, Ciphersuite};
use dalek_ff_group::Ristretto;
use schnorr::aggregate::SchnorrAggregate;

let mut encoded = Vec::new();
encoded.extend(0u32.to_le_bytes());                    // zero Rs
encoded.extend(<Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref()); // s = 0

let aggregate =
  SchnorrAggregate::<Ristretto>::read(&mut encoded.as_slice()).unwrap();

// Four zero bytes plus a zero scalar is accepted as a valid aggregate
// for the empty signer set.
assert!(aggregate.verify(b"example domain", &[]));
```

`SchnorrAggregator::new(b"example domain").complete()` returns `None` for the same empty signing operation, demonstrating that deserialization admits an aggregate the legitimate producer refuses to emit. [6](#0-5)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L76-87)
```rust
  /// Read a SchnorrAggregate from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
```

**File:** crypto/schnorr/src/aggregate.rs (L127-145)
```rust
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

**File:** crypto/schnorr/src/aggregate.rs (L157-185)
```rust
impl<C: Ciphersuite> SchnorrAggregator<C> {
  /// Create a new aggregator.
  ///
  /// The DST used here must prevent a collision with whatever hash function produced the
  /// challenges.
  pub fn new(dst: &'static [u8]) -> Self {
    let mut res = Self { digest: DigestTranscript::<C::H>::new(dst), sigs: vec![] };
    res.digest.domain_separate(b"signatures");
    res
  }

  /// Aggregate a signature.
  pub fn aggregate(&mut self, challenge: C::F, sig: SchnorrSignature<C>) {
    self.digest.append_message(b"challenge", challenge.to_repr());
    self.sigs.push(sig);
  }

  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

    let mut aggregate = SchnorrAggregate { Rs: Vec::with_capacity(self.sigs.len()), s: C::F::ZERO };
    for i in 0 .. self.sigs.len() {
      aggregate.Rs.push(self.sigs[i].R);
      aggregate.s += self.sigs[i].s * weight::<_, C::F>(&mut self.digest);
    }
    Some(aggregate)
```
