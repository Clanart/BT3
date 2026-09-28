### Title
Unauthenticated aggregate-signature input crashes verification via out-of-bounds challenge access - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::verify` can panic while deriving aggregation weights from attacker-supplied aggregate signature bytes. For a 32-byte digest and a scalar whose wide-reduction target is 48 bytes, `weight` attempts to read `bytes[32..40]` before requesting the continuation challenge. [1](#0-0) [2](#0-1) 

### Finding Description
`SchnorrAggregate::read` accepts an attacker-controlled point list and scalar without authentication requirements beyond canonical decoding. [3](#0-2)  Verification passes every nonce and caller-supplied challenge into `weight`, which derives `BYTES = ceil((F::NUM_BITS + 128) / 8)`. [4](#0-3)  For a 256-bit scalar this target is 48 bytes. When the transcript challenge is only 32 bytes, the loop consumes bytes `0..8`, `8..16`, `16..24`, and `24..32`; because `i` then equals the target-relative value 32 but not `bytes.len()`, the continuation branch is skipped. The next iteration slices past the end of the 32-byte challenge. [5](#0-4) 

The aggregation path has the same defect because `SchnorrAggregator::complete` calls the same `weight` routine. [6](#0-5) 

### Impact Explanation
An unprivileged sender can submit syntactically valid aggregate-signature bytes to any reachable `SchnorrAggregate::verify` call backed by a 32-byte digest and cause a panic instead of receiving `false`. In a network-facing verifier, this crashes or aborts the verification task before authentication can complete. The issue affects even a nominally valid one-signature aggregate; no malformed point, non-canonical scalar, private state, or special validator privilege is required. [3](#0-2) [4](#0-3) 

### Likelihood Explanation
The attack requires only that the application deserialize public signature bytes and invoke verification, both of which are normal operations for an aggregate-signature API. The panic is deterministic whenever the scalar-field reduction width exceeds the digest’s challenge width and the first exhaustion occurs exactly at the end of a 32-byte buffer. The trigger does not require cryptanalysis, signature validity, timing, or repeated attempts. [7](#0-6) 

### Recommendation
Rewrite `weight` as an explicit wide-byte stream which checks for digest exhaustion before every 64-bit read, obtains `aggregation_weight_continued`, and resumes at index zero. Alternatively, request a single transcript challenge with enough bytes for the configured field width. Add tests covering 32-, 48-, and 64-byte digest outputs for each supported `Ciphersuite`, plus property tests that `SchnorrAggregate::read` followed by `verify` always returns a boolean rather than panicking. [8](#0-7) 

### Proof of Concept
For a ciphersuite where `F::NUM_BITS == 256` and `C::H` produces a 32-byte digest:

```rust
use schnorr::SchnorrAggregate;
use frost::curve::Secp256k1;

// Encoding: u32 little-endian R count, one canonical R, then canonical s.
let mut encoded = Vec::new();
encoded.extend(1u32.to_le_bytes());
encoded.extend(R.to_bytes());      // attacker-controlled canonical point
encoded.extend(s.to_repr());       // attacker-controlled canonical scalar

let aggregate =
  SchnorrAggregate::<Secp256k1>::read(&mut encoded.as_slice()).unwrap();

// One caller-provided challenge matching the one R.
aggregate.verify(b"application-dst", &[(public_key, challenge)]);
```

Execution reaches `weight` with `BYTES == 48` and `bytes.len() == 32`; after consuming `bytes[24..32]`, the next loop iteration attempts `bytes[32..40]` and panics before `aggregation_weight_continued` is requested. [9](#0-8)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L21-64)
```rust
// Returns a unbiased scalar weight to use on a signature in order to prevent malleability
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

**File:** crypto/schnorr/src/aggregate.rs (L180-184)
```rust
    let mut aggregate = SchnorrAggregate { Rs: Vec::with_capacity(self.sigs.len()), s: C::F::ZERO };
    for i in 0 .. self.sigs.len() {
      aggregate.Rs.push(self.sigs[i].R);
      aggregate.s += self.sigs[i].s * weight::<_, C::F>(&mut self.digest);
    }
```
