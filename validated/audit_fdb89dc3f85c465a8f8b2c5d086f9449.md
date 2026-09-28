### Title
`SchnorrAggregate::verify` returns `true` for a zero-signer aggregate, an erroneous authentication pass on absent input - (File: crypto/schnorr/src/aggregate.rs)

### Summary
CVE-2024-22257 is a broken-access-control flaw in which Spring Security's `AuthenticatedVoter::vote` returns an erroneous `true` when passed a `null` `Authentication`. The bug class is: **an authorization/verification routine that answers "granted/valid" when the credential set is empty or null**, because it never checks that at least one credential was actually presented.

The in-scope analog lives in `crypto/schnorr/src/aggregate.rs`. `SchnorrAggregate::read` accepts a length-prefixed `Rs` vector of length 0 plus a scalar `s` from attacker-controlled bytes, and `SchnorrAggregate::verify` then checks only `Rs.len() == keys_and_challenges.len()` and evaluates the multiexp `sum(z_i*R_i + z_i*c_i*A_i) - s*G == 0`. With zero statements, that reduces to `-s*G == 0`, so the forged aggregate `{ Rs: [], s: 0 }` verifies successfully for **any** `dst` and any empty signer list — an authentication pass with no credentials presented, exactly the shape of the Spring null-authentication bug.

### Finding Description
`SchnorrAggregate::read` reads a `u32` count and that many group elements, then a scalar, with no lower bound on the count [1](#0-0) . `verify` only enforces that the number of `Rs` equals the number of `(key, challenge)` pairs supplied by the caller; when both are empty it pushes no per-signer pairs and computes `multiexp_vartime(&[(-s, G)]).is_identity()` [2](#0-1) . Setting `s = 0` makes this the identity unconditionally, so `verify` returns `true` having verified that *nobody* signed — the null-authentication returns-true pattern. Notably, the producing side already recognizes this as invalid: `SchnorrAggregator::complete` returns `None` when `self.sigs.is_empty()`, so a legitimate aggregator can never emit an empty aggregate [3](#0-2) . The verifier does not enforce the symmetric constraint, so a byte-level forger can construct an object the honest code path refuses to produce and have it accepted.

The same "empty input passes" shape appears in `BatchVerifier::verify`/`verify_vartime`, where an empty statement list multiexps to the identity and returns `true` (explicitly asserted valid by `test_batch`) [4](#0-3) . That is only a problem if a caller ever queues zero statements — the `SchnorrAggregate` path is the stronger, directly byte-reachable instance since `SchnorrAggregate::read` is an untrusted-input entry point.

### Impact Explanation
An unprivileged party can forge a `SchnorrAggregate` serialization — four zero bytes for the length plus a canonical zero `s` — that passes `verify` for every domain separator and every message whenever the caller's signer set is empty. In Serai the concrete consumer (`Validators::verify_aggregate`) additionally requires a weight threshold before accepting a commit, which mitigates the empty-signer case there, but the primitive itself exports a verifier that authenticates an absent credential set as valid. Any caller that treats a successful `verify` as proof "these validators signed" — e.g., one that derives the signer set from the signature rather than independently constraining it — accepts a signature over an arbitrary message with no key holder involved. Severity: Medium (forged signature primitive; real-world impact gated by caller-side signer-set checks).

### Likelihood Explanation
The forgery requires only bytes the attacker fully controls: `SchnorrAggregate::read` is listed as an untrusted-input entry point, and the forged encoding is 36 bytes. Exploitation requires a call site that verifies an aggregate against a signer list it does not independently bound to be non-empty; within the audited in-scope surface this is a latent footgun rather than a demonstrated end-to-end exploit, hence Medium rather than High.

### Recommendation
In `SchnorrAggregate::verify` (crypto/schnorr/src/aggregate.rs), reject `self.Rs.is_empty()` before evaluating the multiexp, mirroring `SchnorrAggregator::complete`'s `None` on empty input. Optionally also reject a zero-length aggregate in `SchnorrAggregate::read` so the invalid object cannot be deserialized at all.

### Proof of Concept
```rust
// crypto/schnorr — conceptual PoC against SchnorrAggregate::verify
// Forged encoding: u32 count = 0, s = 0
let mut forged = Vec::new();
forged.extend_from_slice(&0u32.to_le_bytes());        // zero Rs
forged.extend_from_slice(C::F::ZERO.to_repr().as_ref());

let agg = SchnorrAggregate::<C>::read(&mut forged.as_slice()).unwrap();
// Verifies as a valid aggregate signature for ANY dst and ANY message,
// despite no signer existing:
assert!(agg.verify(b"any_dst", &[]));
```
`verify` evaluates `multiexp_vartime(&[(C::F::ZERO negated... )])` = `-0*G` = identity → `true`. No legitimate `SchnorrAggregator` can produce this object (`complete` returns `None` on empty `sigs`), confirming it is a verifier-only acceptance of absent authentication — the direct analog of `AuthenticatedVoter::vote(null) == true`.

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
