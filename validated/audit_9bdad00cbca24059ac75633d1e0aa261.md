### Title
Empty `SchnorrAggregate` trivially passes verification — (File: crypto/schnorr/src/aggregate.rs)

### Summary

Analogous to CVE-2022-42426's "lack of proper validation of a user-supplied string before using it", `SchnorrAggregate::verify` never validates that the aggregate actually contains any signature components. An aggregate signature with `Rs = []` and `s = 0` — fully expressible via attacker-controlled bytes fed to `SchnorrAggregate::read` — makes the verification multiexp consist of a single term `-s·G = identity`, so `multiexp_vartime(...).is_identity()` returns true and the forged aggregate is accepted.

### Finding Description

`SchnorrAggregate::read` reads a `u32` length `len`, then `len` points and one scalar. `len = 0` is accepted, and `s` is read via `C::read_F`, for which an all-zero encoding is canonical: [1](#0-0) 

`verify` then only checks `self.Rs.len() != keys_and_challenges.len()` — satisfied when both are empty — builds a transcript that absorbs zero challenges, and constructs `pairs` containing only `(-self.s, C::generator())`: [2](#0-1) 

With `s = 0`, `multiexp_vartime(&[(0, G)])` yields the identity element, so `verify` returns `true`. The same class of hole exists in the underlying `BatchVerifier`: its own tests codify that an empty batch "verifies" (`valid(batch)` on `BatchVerifier::new(0)`), because `flat` of an empty statement list produces an empty multiexp which is identity: [3](#0-2) [4](#0-3) 

Note the first-statement weight is fixed to `ONE` in `BatchVerifier::queue`, so a single queued statement is checked unweighted — acceptable for that statement's own correctness but reinforcing that the verifier performs no sanity check on the quantity of statements: [5](#0-4) 

### Impact Explanation

Any caller that treats `SchnorrAggregate::verify(dst, keys_and_challenges)` as proof that "the aggregate attests to these keys/challenges" can be defeated when the claim set is empty or becomes empty through attacker influence (e.g., all entries filtered out before verification). The attacker supplies 4 bytes `0x00000000` plus 32 zero bytes; `read` succeeds, and `verify` returns true without any secret, nonce, or valid signature existing. This is a forged-signature acceptance: the verifier formula is satisfied by a vacuous statement rather than by knowledge of a discrete logarithm, matching the required acceptance criterion of "a forged proof or signature" / "an incorrect verifier formula".

### Likelihood Explanation

Exploitation requires a downstream consumer that verifies an aggregate against a `keys_and_challenges` list the attacker can empty (or a consumer that accepts empty claim sets). The serialization path is public and reachable with untrusted bytes: `SchnorrAggregate::read` is the documented deserialization entry point and performs canonical validation only of encodings, not semantic non-emptiness. No threshold of honest parties, key leakage, or collusion is needed — only a code path where an empty aggregate reaches `verify`.

### Recommendation

In `SchnorrAggregate::verify`, reject `self.Rs.is_empty()` (equivalently, reject `keys_and_challenges.is_empty()`) before building pairs — an aggregate over zero signatures is not a signature of anything. Similarly, `SchnorrAggregate::read` could reject `len == 0` at deserialization. Optionally, `BatchVerifier::verify*` should return `false` for an empty batch, since "all zero statements hold" is vacuously true and likely reflects an integration bug rather than a verified fact.

### Proof of Concept

```rust
use schnorr::aggregate::SchnorrAggregate;
use ciphersuite::Ciphersuite;

// Any ciphersuite, e.g. C = Ristretto or Secp256k1
fn forge<C: Ciphersuite>() {
    // Attacker-controlled bytes: u32 len = 0, then s = 0
    let mut bytes = vec![0u8; 4];
    bytes.extend_from_slice(C::F::ZERO.to_repr().as_ref());
    let mut slice = bytes.as_slice();

    let agg = SchnorrAggregate::<C>::read(&mut slice).unwrap();

    // Rs == [], s == 0. For any caller-supplied dst, verifying against an
    // empty keys_and_challenges list reduces to checking -0*G == identity.
    assert!(agg.verify(b"any-domain-separator", &[]));
}
```

Root cause: `crypto/schnorr/src/aggregate.rs:128-145` — no non-emptiness validation of `Rs`/`keys_and_challenges` before `multiexp_vartime(&pairs).is_identity()`, so `-s·G` with `s = 0` satisfies the equation vacuously.

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

**File:** crypto/multiexp/src/batch.rs (L45-48)
```rust
    // Define a unique scalar factor for this set of variables so individual items can't overlap
    let u = if self.0.is_empty() {
      G::Scalar::ONE
    } else {
```

**File:** crypto/multiexp/src/batch.rs (L92-101)
```rust
  #[must_use]
  pub fn verify(&self) -> bool {
    multiexp(&flat(&self.0)).is_identity().into()
  }

  /// Perform batch verification in variable time.
  #[must_use]
  pub fn verify_vartime(&self) -> bool {
    multiexp_vartime(&flat(&self.0)).is_identity().into()
  }
```

**File:** crypto/multiexp/src/tests/batch.rs (L29-31)
```rust
  // Test an empty batch
  let batch = BatchVerifier::new(0);
  valid(batch);
```
