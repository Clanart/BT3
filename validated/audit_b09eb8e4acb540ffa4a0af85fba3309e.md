### Title
Empty aggregate Schnorr signatures are accepted as valid - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero signatures when `s` is the zero scalar. This produces a forged aggregate signature for an empty signer set.

### Finding Description
`SchnorrAggregate::read` permits a zero-length `Rs` vector and reads `s` without requiring a non-empty aggregate. [1](#0-0)  During verification, matching lengths are checked, but an empty `keys_and_challenges` slice satisfies that check. [2](#0-1)  The resulting multiexponentiation contains only `(-s)G`; with `s = 0`, it evaluates to identity and returns true. [3](#0-2)  This state cannot be produced honestly because `SchnorrAggregator::complete` explicitly returns `None` when no signatures were aggregated. [4](#0-3) 

### Impact Explanation
An attacker can supply a 36-byte serialized aggregate consisting of a zero count and zero scalar and have it accepted as a valid aggregate signature for zero signers. Any caller that treats `verify` as proof that at least one listed signer authorized a message, or that calls it with an attacker-controlled empty signer list, can accept an authorization proof no signer produced. Downstream quorum checks may reduce the impact where they independently reject empty signer sets.

### Likelihood Explanation
The serialized input is short, canonical, and does not require knowledge of any private key or nonce. Exploitation requires a reachable verification path to invoke `SchnorrAggregate::verify` with an empty signer list or otherwise regard successful verification of the supplied signer list as sufficient authorization. [5](#0-4) 

### Recommendation
Reject empty aggregate signatures during both deserialization and verification. `SchnorrAggregate::read` should return an error when `len == 0`, and `SchnorrAggregate::verify` should independently return `false` when `self.Rs.is_empty()` or `keys_and_challenges.is_empty()`. Add a regression test proving that the serialized zero-count, zero-scalar aggregate is rejected.

### Proof of Concept
```rust
use schnorr::SchnorrAggregate;
use dalek_ff_group::Ristretto;

let encoded = [0u8; 36]; // u32 count = 0, followed by canonical scalar s = 0
let aggregate =
  SchnorrAggregate::<Ristretto>::read(&mut encoded.as_slice()).unwrap();

// Incorrectly accepted despite containing no signer nonce and no signers.
assert!(aggregate.verify(b"test-dst", &[]));
```

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

**File:** crypto/schnorr/src/aggregate.rs (L127-135)
```rust
  pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
    if self.Rs.len() != keys_and_challenges.len() {
      return false;
    }

    let mut digest = DigestTranscript::<C::H>::new(dst);
    digest.domain_separate(b"signatures");
    for (_, challenge) in keys_and_challenges {
      digest.append_message(b"challenge", challenge.to_repr());
```

**File:** crypto/schnorr/src/aggregate.rs (L138-145)
```rust
    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```

**File:** crypto/schnorr/src/aggregate.rs (L174-178)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }
```

**File:** coordinator/tributary/src/tendermint/mod.rs (L207-218)
```rust
    let Ok(aggregate) = SchnorrAggregate::<Ristretto>::read::<&[u8]>(&mut sig.as_slice()) else {
      return false;
    };

    if signers.len() != aggregate.Rs().len() {
      return false;
    }

    let mut challenges = vec![];
    for (key, nonce) in signers.iter().zip(aggregate.Rs()) {
      challenges.push(challenge(self.genesis, *key, nonce.to_bytes().as_ref(), msg));
    }
```
