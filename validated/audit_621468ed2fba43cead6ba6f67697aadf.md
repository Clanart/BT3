### Title
Unbound aggregation weights allow forging aggregate Schnorr signatures for arbitrary public keys - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` derives each signature’s aggregation weight only from the caller-supplied challenge transcript, without binding the weight to the corresponding public key or attacker-controlled nonce commitment `R`. An attacker can deserialize arbitrary `Rs` and `s`, compute the deterministic weights, and choose nonce commitments so the verification equation holds for public keys and challenges they do not control. This permits a forged aggregate signature accepted by `verify`.

### Finding Description
`SchnorrAggregate::read` accepts an attacker-controlled vector of `R` values and scalar `s` from serialized input. [1](#0-0)  During verification, the transcript is initialized with the verifier’s domain separator, then only the externally supplied challenges are appended. [2](#0-1)  Each weight is derived sequentially from this transcript before being applied to the corresponding `R` and public-key term. [3](#0-2)  The final equation is `sum_i z_i * R_i + sum_i z_i * c_i * P_i - s * G = 0`. [4](#0-3)  Because neither `R_i` nor `P_i` is included in the derivation of `z_i`, the prover can predict every `z_i` and select `R_i = z_i^-1 * k_i * G - c_i * P_i` for arbitrary scalars `k_i` satisfying `sum_i k_i = s`, making the equation hold without knowing any private key. [5](#0-4) [4](#0-3) 

### Impact Explanation
Any verifier relying on `SchnorrAggregate::verify` can accept an aggregate signature over public keys and challenges whose holders never signed. This is a direct signature forgery against the aggregate verification formula and can authenticate attacker-selected statements under victim keys wherever the API is used for authorization.

### Likelihood Explanation
The attacker only needs to submit a serialized `SchnorrAggregate` or otherwise control its `Rs` and `s`; the target public keys and challenges are already inputs to verification. The weights are deterministic and publicly computable, so exploitation requires only field and group arithmetic, not private-key knowledge, nonce leakage, collusion, or malformed curve encodings.

### Recommendation
Bind each aggregation weight to the complete signature statement being aggregated, including at minimum the public key, nonce commitment, challenge, and positional index. For example, append each `(P_i, R_i, c_i)` tuple to the transcript before deriving `z_i`, and have `SchnorrAggregator::aggregate` use the same tuple-bound derivation. Reject identity or otherwise invalid points through `C::read_G`, preserve canonical serialization, and reject unexpected trailing bytes. Add negative tests demonstrating that modifying any `R`, public key, or challenge changes the expected weight and invalidates a forged aggregate.

### Proof of Concept
For target keys `P_i`, challenges `c_i`, domain `dst`, and chosen scalar `s`, the attacker computes each public weight `z_i` by replaying the transcript construction used by `verify`. They then choose `k_1 .. k_n` with `sum_i k_i = s` and set:

```text
R_i = z_i^-1 * k_i * G - c_i * P_i
```

The submitted aggregate is `(R_1 .. R_n, s)`. Verification computes:

```text
sum_i z_i * R_i + sum_i z_i * c_i * P_i - s * G
= sum_i (k_i * G - z_i * c_i * P_i) + sum_i z_i * c_i * P_i - s * G
= sum_i k_i * G - s * G
= 0
```

Thus `multiexp_vartime` returns identity and `verify` returns true despite the attacker not knowing the discrete logarithm of any victim `P_i`.

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L22-65)
```rust
fn weight<D: Send + Clone + SecureDigest, F: PrimeField>(digest: &mut DigestTranscript<D>) -> F {
  let mut bytes = digest.challenge(b"aggregation_weight");
  debug_assert_eq!(bytes.len() % 8, 0);
  // This should be guaranteed thanks to SecureDigest
  debug_assert!(bytes.len() >= 32);

  let mut res = F::ZERO;
  let mut i = 0;

  // Derive a scalar from enough bits of entropy that bias is < 2^128
  // This can't be const due to its usage of a generic
  // Also due to the usize::try_from, yet that could be replaced with an `as`
  #[allow(non_snake_case)]
  let BYTES: usize = usize::try_from((F::NUM_BITS + 128).div_ceil(8)).unwrap();

  let mut remaining = BYTES;

  // We load bits in as u64s
  const WORD_LEN_IN_BITS: usize = 64;
  const WORD_LEN_IN_BYTES: usize = WORD_LEN_IN_BITS / 8;

  let mut first = true;
  while i < remaining {
    // Shift over the already loaded bits
    if !first {
      for _ in 0 .. WORD_LEN_IN_BITS {
        res += res;
      }
    }
    first = false;

    // Add the next 64 bits
    res += F::from(u64::from_be_bytes(bytes[i .. (i + WORD_LEN_IN_BYTES)].try_into().unwrap()));
    i += WORD_LEN_IN_BYTES;

    // If we've exhausted this challenge, get another
    if i == bytes.len() {
      bytes = digest.challenge(b"aggregation_weight_continued");
      remaining -= i;
      i = 0;
    }
  }
  res
}
```

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

**File:** crypto/schnorr/src/aggregate.rs (L132-136)
```rust
    let mut digest = DigestTranscript::<C::H>::new(dst);
    digest.domain_separate(b"signatures");
    for (_, challenge) in keys_and_challenges {
      digest.append_message(b"challenge", challenge.to_repr());
    }
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
