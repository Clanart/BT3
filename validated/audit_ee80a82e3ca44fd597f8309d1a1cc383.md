### Title
Aggregated Schnorr signatures with multiple members are rejected by mismatched weight derivation - (File: `crypto/schnorr/src/aggregate.rs`)

### Summary
`SchnorrAggregate::verify` derives aggregation weights from a transcript state that does not match the state used by `SchnorrAggregator::complete`. Verification appends every signature challenge before deriving the first weight, while aggregation derives each weight immediately after appending that signature’s challenge. As a result, any aggregate containing at least two signatures uses different weights during verification than during aggregation and fails verification despite being correctly formed. [1](#0-0) [2](#0-1) 

### Finding Description
`SchnorrAggregator::aggregate` appends the current signature challenge to `self.digest`, and `complete` then calls `weight(&mut self.digest)` once per stored signature. [2](#0-1)  Therefore, the first weight commits only to `challenge[0]`, while the second weight commits to `challenge[0]` followed by `challenge[1]`. [2](#0-1) 

`SchnorrAggregate::verify` instead appends all challenges in a preliminary loop and only afterward derives every weight. [3](#0-2)  This makes every verification weight, including the first, commit to the complete challenge list rather than the incremental transcript state used during aggregation. [4](#0-3) 

The verifier equation is `sum_i z_i * R_i + sum_i z_i * c_i * A_i - s * G = 0`, but `s` was computed using the aggregation-time `z_i` values while verification recomputes different `z_i` values. [5](#0-4) [6](#0-5)  For an aggregate of `n >= 2` signatures, the equality consequently does not hold except with negligible accidental weight collisions. [7](#0-6) [8](#0-7) 

### Impact Explanation
This is an incorrect verifier formula over untrusted bytes supplied through `SchnorrAggregate::read` and checked by `SchnorrAggregate::verify`. [9](#0-8) [10](#0-9)  A validly generated multi-signature aggregate is reported invalid, which can prevent acceptance of otherwise correct aggregated signatures in any production caller relying on this API. [11](#0-10) [10](#0-9) 

The issue does not create an obvious forgery path because the verifier uses a stricter but different linear combination than the prover. [5](#0-4)  Its practical effect is denial of valid aggregate signatures and potential rejection of a batch containing otherwise valid proofs. [10](#0-9) 

### Likelihood Explanation
Any caller that aggregates two or more signatures reaches the mismatch deterministically. [12](#0-11)  No malicious participant, leaked key, invalid curve input, or unusual preprocessing is required; ordinary calls to `aggregate`, `complete`, and `verify` expose the bug. [12](#0-11) [10](#0-9) 

Single-signature aggregates are unaffected because there is only one appended challenge before the sole weight derivation, making the two transcript states coincide. [4](#0-3) [2](#0-1) 

### Recommendation
Make verification replay the same incremental transcript sequence used by aggregation: derive each weight immediately after appending the corresponding challenge, or change aggregation to append all challenges before deriving every weight. [4](#0-3) [2](#0-1) 

For example, verification can remove the preliminary loop over all challenges and instead append `challenge` inside the weight-generation loop before calling `weight`. [4](#0-3)  Add a regression test that aggregates at least two distinct valid Schnorr signatures and asserts that `SchnorrAggregate::verify` accepts the result. [13](#0-12) 

### Proof of Concept
1. Create two valid `SchnorrSignature<C>` values under distinct challenges `c0` and `c1`.
2. Call `let mut agg = SchnorrAggregator::new(dst)`.
3. Call `agg.aggregate(c0, sig0)` followed by `agg.aggregate(c1, sig1)`.
4. Call `let aggregate = agg.complete().unwrap()`.
5. Call `aggregate.verify(dst, &[(key0, c0), (key1, c1)])`.
6. Aggregation computes `s = s0 * H(c0) + s1 * H(c0 || c1)`, while verification effectively tests against weights derived only after committing to `c0 || c1`, producing `z0' * R0 + z0' * c0 * A0 + z1' * R1 + z1' * c1 * A1 - sG != 0` for `z0' != H(c0)`. [2](#0-1) [1](#0-0) 
7. The valid two-signature aggregate returns `false` despite both component signatures satisfying `s_iG = R_i + c_iA_i`. [14](#0-13) [5](#0-4)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L76-88)
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
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L127-146)
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
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L169-185)
```rust
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

**File:** crypto/schnorr/src/lib.rs (L88-110)
```rust
  pub fn batch_statements(&self, public_key: C::G, challenge: C::F) -> [(C::F, C::G); 3] {
    // s = r + ca
    // sG == R + cA
    // R + cA - sG == 0
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
  }

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
  }
```
