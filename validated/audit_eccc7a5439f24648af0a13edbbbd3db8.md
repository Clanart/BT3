### Title
Empty serialized Schnorr aggregate verifies as valid - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts an aggregate with zero `Rs`, while `SchnorrAggregate::verify` treats an empty aggregate with `s = 0` as valid because the resulting multiexponentiation evaluates to the identity element. [1](#0-0) [2](#0-1) 

### Finding Description
The deserializer reads a four-byte length, accepts a length of zero without checking it, and then reads `s`. [1](#0-0)  Verification checks only that `Rs.len() == keys_and_challenges.len()`, appends `-s * G`, and tests whether the multiexponentiation is the identity. [2](#0-1)  Therefore, when both lists are empty and `s` is zero, the only pair is `0 * G`, making verification succeed despite no signature being present. [3](#0-2)  The canonical aggregation path explicitly refuses to produce an aggregate when no signatures were supplied, establishing that an empty aggregate is not intended to be valid. [4](#0-3) 

### Impact Explanation
An unprivileged party can submit an all-zero serialized aggregate and cause a verifier expecting at least one signature to accept an empty proof of authorization. [1](#0-0)  This is a signature-verification false positive caused by treating the empty entry set as a valid aggregate rather than rejecting it. [2](#0-1) 

### Likelihood Explanation
The malicious encoding is deterministic and compact: a zero `u32` length followed by a zero scalar. [1](#0-0)  Exploitation requires the verifier to call `verify` with an empty `keys_and_challenges` list, which is precisely the dangerous state produced when empty or default entries are not filtered before verification. [5](#0-4) 

### Recommendation
Reject empty aggregates in both `SchnorrAggregate::read` and `SchnorrAggregate::verify`, matching the invariant already enforced by `SchnorrAggregator::complete`. [4](#0-3)  Verification should return `false` when `self.Rs.is_empty()` or `keys_and_challenges.is_empty()`. [2](#0-1) 

### Proof of Concept
```rust
// 0u32 little-endian R count || canonical zero scalar
let encoded = [0u8; 36];
let aggregate =
  SchnorrAggregate::<C>::read(&mut &encoded[..]).expect("empty aggregate accepted");

assert!(aggregate.verify(b"example_dst", &[]));
```

The read path performs no non-empty check, and verification succeeds because `-0 * G` is the identity. [1](#0-0) [3](#0-2)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-87)
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

**File:** crypto/schnorr/src/aggregate.rs (L175-178)
```rust
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }
```
