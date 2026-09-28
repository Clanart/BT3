### Title
Empty aggregate signature verifies as valid - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero nonces and a zero `s` scalar. [1](#0-0)  Although `SchnorrAggregator::complete` refuses to create an empty aggregate, deserialization does not enforce that same invariant. [2](#0-1) 

### Finding Description
`SchnorrAggregate::read` accepts a zero nonce count and a canonical zero scalar. [3](#0-2)  During verification, an empty `keys_and_challenges` slice produces no per-signer statements, leaving only `(-s) * G`; when `s == 0`, that statement is the identity and verification succeeds. [4](#0-3)  The Tributary adapter exposes this path through `Validators::verify_aggregate`, which accepts caller-controlled aggregate bytes and only requires the encoded nonce count to equal the supplied signer count. [5](#0-4) 

### Impact Explanation
A caller that treats `verify_aggregate(..., &[], msg, forged)` as proof of authorization can accept a signature created without any signer. [5](#0-4)  This violates the aggregate signature’s own construction invariant, since honest aggregation explicitly returns `None` when no signatures were supplied. [6](#0-5) 

### Likelihood Explanation
The forged encoding is deterministic and requires no secret material: a zero nonce count followed by a zero scalar. [3](#0-2)  Exploitability depends on a caller invoking aggregate verification with an empty signer set; consensus code that separately enforces voting weight would reject that set before relying on this result. [7](#0-6) 

### Recommendation
Reject empty aggregates in `SchnorrAggregate::verify`, and preferably reject them in `SchnorrAggregate::read` as well. [3](#0-2)  The verifier should enforce the same non-empty invariant as `SchnorrAggregator::complete` before evaluating the multiexponentiation. [2](#0-1) 

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use dalek_ff_group::Ristretto;
use schnorr::aggregate::SchnorrAggregate;

const DST: &[u8] = b"Tributary Tendermint Commit Aggregator";

// Encoding: u32 nonce_count || nonce encodings || scalar.
let mut encoded = Vec::from(0u32.to_le_bytes());
encoded.extend(<Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref());

let aggregate =
  SchnorrAggregate::<Ristretto>::read(&mut encoded.as_slice()).unwrap();

assert!(aggregate.verify(DST, &[]));
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

**File:** crypto/schnorr/src/aggregate.rs (L174-185)
```rust
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

**File:** coordinator/tributary/src/tendermint/mod.rs (L200-228)
```rust
  #[must_use]
  fn verify_aggregate(
    &self,
    signers: &[Self::ValidatorId],
    msg: &[u8],
    sig: &Self::AggregateSignature,
  ) -> bool {
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

    aggregate.verify(
      DST,
      signers
        .iter()
        .zip(challenges)
        .map(|(s, c)| (<Ristretto as Ciphersuite>::read_G(&mut s.as_slice()).unwrap(), c))
        .collect::<Vec<_>>()
        .as_slice(),
    )
```

**File:** coordinator/tributary/tendermint/src/ext.rs (L131-165)
```rust
/// A commit for a specific block.
///
/// The list of validators have weight exceeding the threshold for a valid commit.
#[derive(PartialEq, Debug, Encode, Decode)]
pub struct Commit<S: SignatureScheme> {
  /// End time of the round which created this commit, used as the start time of the next block.
  pub end_time: u64,
  /// Validators participating in the signature.
  pub validators: Vec<S::ValidatorId>,
  /// Aggregate signature.
  pub signature: S::AggregateSignature,
}

impl<S: SignatureScheme> Clone for Commit<S> {
  fn clone(&self) -> Self {
    Self {
      end_time: self.end_time,
      validators: self.validators.clone(),
      signature: self.signature.clone(),
    }
  }
}

/// Weights for the validators present.
pub trait Weights: Send + Sync {
  type ValidatorId: ValidatorId;

  /// Total weight of all validators.
  fn total_weight(&self) -> u64;
  /// Weight for a specific validator.
  fn weight(&self, validator: Self::ValidatorId) -> u64;
  /// Threshold needed for BFT consensus.
  fn threshold(&self) -> u64 {
    ((self.total_weight() * 2) / 3) + 1
  }
```
