### Title
`SchnorrAggregate::verify` accepts a forged aggregate signature over an empty signer set - (File: crypto/schnorr/src/aggregate.rs)

### Summary
The external report describes an invariant (`msg.value > 0`) that is silently vacated when a correlated quantity (`data.length`) is zero, letting users get the effect of a paid message for free. The same shape exists in Serai's half-aggregation verifier: `SchnorrAggregate::verify` validates `sum(z_i * R_i) + sum(z_i * c_i * P_i) == s * G`, but when the signature carries zero `Rs` and the caller's `keys_and_challenges` list is empty, the entire left side is vacuous — there is no weight bound to any public key at all — and verification reduces to checking `s * G == 0`, which any attacker satisfies with `s = 0`. A deserializer happily produces exactly that forgery from public bytes.

### Finding Description
`SchnorrAggregate::read` reads a `u32` count and then that many `R` points, with no minimum: [1](#0-0) 

`verify` then only checks `self.Rs.len() != keys_and_challenges.len()`, builds `pairs` from the (empty) iteration, pushes a single `(-s, generator)` term, and accepts iff the multiexp is identity: [2](#0-1) 

With both lists empty, `pairs = [(-s, G)]` and the check is `(-s) * G == identity`, i.e. `s == 0`. The digest/weight machinery (`weight()` over `keys_and_challenges`) never runs, so nothing binds the result to any key, nonce, or challenge — the exact analog of a fee check that degenerates to `fees += 0` for zero-length data. Note the honest aggregation path, `SchnorrAggregator::complete`, refuses empty input (`if self.sigs.is_empty() { return None }`), confirming the intended invariant "an aggregate must cover ≥1 signature" — yet `read`/`verify` do not enforce it on untrusted bytes: [3](#0-2) 

### Impact Explanation
Any party can construct a `SchnorrAggregate` encoding (`0u32` length prefix + zero scalar) that `SchnorrAggregate::verify` accepts for any `dst` whenever the verifying set is empty. This is a forged signature accepted by an incorrect verifier formula: the function promises a check binding each signature's weight to a public key/challenge, yet delivers a vacuous check satisfiable by a constant crafted value. Wherever downstream consensus or recovery logic accepts an aggregate over a set that can be empty (or derives the accepted set from the attacker-controlled `Rs` count), this is an unconditional forgery.

### Likelihood Explanation
Reachable entirely through public inputs: the forgery bytes flow through `SchnorrAggregate::read`, explicitly in scope as a `read`/`verify` sink, and `verify` is `#[must_use]` returning `true`. Exploitation requires a call site that invokes `verify` with an empty `keys_and_challenges` slice (or one whose length the attacker zeroes via the serialized `Rs` count mismatch path — although mismatched lengths correctly return `false`, so the empty-empty case is the reachable one). Because `read` enforces no minimum while `complete` does, any serde-adjacent acceptance of aggregate signatures inherits the hole.

### Recommendation
Mirror the producer-side invariant in the consumer: reject empty aggregates. In `SchnorrAggregate::verify`, return `false` when `self.Rs.is_empty()` (equivalently, when `keys_and_challenges.is_empty()`), and/or have `SchnorrAggregate::read` reject a zero length. A one-line guard suffices:

```rust
if self.Rs.is_empty() || (self.Rs.len() != keys_and_challenges.len()) {
  return false;
}
```

### Proof of Concept
```rust
use ciphersuite::{Ciphersuite, group::ff::Field};
use schnorr::aggregate::SchnorrAggregate;

// For any C: Ciphersuite
let forged_bytes: Vec<u8> = {
  let mut v = vec![];
  v.extend(0u32.to_le_bytes());              // zero Rs
  v.extend(C::F::ZERO.to_repr().as_ref());   // s = 0
  v
};
let agg = SchnorrAggregate::<C>::read(&mut forged_bytes.as_slice()).unwrap();
assert!(agg.verify(b"any dst", &[]));        // vacuously passes: forged signature accepted
```
`verify` returns `true` because the multiexp consists solely of `(-0) * G == identity`, despite no key, nonce, or challenge ever being bound.

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

**File:** crypto/schnorr/src/aggregate.rs (L174-186)
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
  }
```
