### Title
Empty aggregate signature accepted as valid - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::read` accepts an aggregate containing zero `R` values, and `SchnorrAggregate::verify` accepts that aggregate when `s = 0` and the verifier is asked to validate an empty challenge/key list. [1](#0-0) [2](#0-1) 

### Finding Description
`SchnorrAggregate::read` trusts a 32-bit count and permits that count to be zero, after which it reads only the aggregate scalar `s`. [3](#0-2)  A scalar encoding of zero is a canonical field encoding accepted by `C::read_F`, so the input can deserialize successfully. [4](#0-3)  During verification, an empty `keys_and_challenges` list produces no weighted signature terms, while `s = 0` makes the sole generator term `-sG` the identity. [2](#0-1) 

### Impact Explanation
An unprivileged caller can submit bytes representing an empty aggregate signature and cause verification over an empty statement set to succeed. [5](#0-4)  This creates a forged aggregate signature because `SchnorrAggregator::complete` refuses to produce an aggregate when no signatures were supplied, making the accepted serialization unreachable through the honest aggregation path. [6](#0-5) 

### Likelihood Explanation
The input is only four zero bytes for the aggregate length followed by the canonical encoding of scalar zero. [3](#0-2)  No secret, malformed point, overflow, panic, or protocol race is required; the bypass follows directly from accepting an empty aggregate and then evaluating the empty multi-scalar equation as identity. [7](#0-6) 

### Recommendation
Reject empty aggregates in both `SchnorrAggregate::read` and `SchnorrAggregate::verify`, matching the existing invariant in `SchnorrAggregator::complete`. [8](#0-7)  A defense-in-depth check should also reject an empty `keys_and_challenges` list before constructing the verification equation. [9](#0-8) 

### Proof of Concept
```rust
// crypto/schnorr/src/aggregate.rs
let mut encoded = vec![0, 0, 0, 0]; // Rs length = 0
encoded.extend(C::F::ZERO.to_repr().as_ref());

let aggregate = SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();
assert!(aggregate.verify(dst, &[]));
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

**File:** crypto/schnorr/src/aggregate.rs (L175-185)
```rust
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
