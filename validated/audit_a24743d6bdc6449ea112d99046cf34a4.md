### Title
Unauthenticated Schnorr aggregate signature forgery - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts attacker-constructed aggregate signatures for arbitrary public keys and challenges because it only checks the weighted batch equation `sum(z_i * (R_i + c_i * P_i)) == s * G`. An attacker can choose every `R_i` with a known discrete logarithm relative to `-c_i * P_i`, then set `s` to the corresponding weighted sum. The verification transcript derives the weights only from the supplied challenges, so this requires no private key or previously valid signature. [1](#0-0) 

### Finding Description
Verification builds deterministic weights with `weight(&mut digest)` and queues `(z_i, R_i)` and `(z_i * c_i, P_i)` into `multiexp_vartime`, followed by `(-s, G)`. [2](#0-1) 

For each entry, an attacker can select an arbitrary scalar `y_i` and set:

```text
R_i = y_i * G - c_i * P_i
```

Then `R_i + c_i * P_i = y_i * G`. After reproducing the verifier's deterministic `z_i` values, the attacker sets:

```text
s = sum(z_i * y_i)
```

This makes the final multiexponentiation exactly `sum(z_i * y_i * G) - s * G = 0`, causing `is_identity()` to return true without knowledge of any `P_i` discrete logarithm. [3](#0-2) 

The issue is reachable through public inputs: `SchnorrAggregate::read` accepts an attacker-controlled list of group elements and scalar `s`, while `verify` accepts the public keys and challenges to authenticate. [4](#0-3) 

### Impact Explanation
Any caller relying on `SchnorrAggregate::verify` as authentication can accept a forged aggregate signature for keys and messages it does not control. This bypasses signature verification rather than merely causing denial of service or accepting malformed encoding. [5](#0-4) 

### Likelihood Explanation
The attack is deterministic and uses only public keys, public challenges, and attacker-chosen proof bytes. No private key share, threshold collusion, malformed point, or protocol-level privilege is needed. The attacker only needs the deterministic weight-generation transcript, which is fully reproducible from the public `dst` and challenge list. [6](#0-5) 

### Recommendation
Do not expose `SchnorrAggregate` as a standalone authentication signature under this verification formula. Redesign aggregation so the verifier authenticates the constituent signatures or the aggregated `s` cannot be freely selected after choosing nonce commitments with known discrete logarithms. At minimum, this requires a different aggregation protocol and security analysis; merely adding `R_i` or `P_i` to the weight transcript does not prevent setting `R_i = y_i * G - c_i * P_i` and `s = sum(z_i * y_i)`. [7](#0-6) 

### Proof of Concept
For each public key/challenge pair `(P_i, c_i)`:

```rust
// y_i is attacker-chosen.
let y_i = C::F::random(rng);

// Construct a nonce commitment whose effective discrete logarithm is y_i.
let R_i = (C::generator() * y_i) - (*P_i * c_i);
```

Submit `Rs = [R_0, ..., R_n]` and compute the verifier's weights:

```rust
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
for (_, challenge) in keys_and_challenges {
  digest.append_message(b"challenge", challenge.to_repr());
}

let mut s = C::F::ZERO;
for (i, y_i) in ys.iter().enumerate() {
  let z_i = weight::<_, C::F>(&mut digest);
  s += z_i * y_i;
}
```

The verifier computes:

```text
sum(z_i * (R_i + c_i * P_i)) - s * G
= sum(z_i * y_i * G) - sum(z_i * y_i * G)
= identity
```

Therefore `SchnorrAggregate { Rs, s }.verify(dst, keys_and_challenges)` returns true despite no valid signature from the owners of `P_i`. [8](#0-7)

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
