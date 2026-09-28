### Title
Empty `SchnorrAggregate` trivially verifies — zero-length signer set satisfies the verification equation - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
The Port3 incident class is a boundary condition where a value of `0` vacuously satisfies an authorization check, permitting a privileged operation without authorization. The analogous shape in Serai is `SchnorrAggregate::verify`: when the aggregate contains zero nonces (`Rs.len() == 0`) and `s == 0`, the only multiexp pair is `(-0) * G == identity`, so verification returns `true` without checking any public key or challenge. `SchnorrAggregate::read` accepts this encoding (`len = 0` followed by a zero scalar) directly from untrusted bytes, and neither `read` nor `verify` rejects an empty aggregate.

### Finding Description
`SchnorrAggregate::read` reads a `u32` count, then that many `Rs`, then `s`, with no minimum-count check — `len = 0`, `s = 0` is accepted [1](#0-0) . In `verify`, the only guard is `self.Rs.len() != keys_and_challenges.len()`; for the empty case this passes, the loop appends no pairs, and the sole pair `(-self.s, C::generator())` with `s = 0` makes `multiexp_vartime(&pairs).is_identity()` return `true` [2](#0-1) . The same "zero passes" pattern exists for any *single* entry where the caller-supplied `(key, challenge)` has `key == identity` — `z*R + z*c*identity - s*G == 0` is satisfiable with `R = identity * ... ` only if `R` were admissible, but `C::read_G` rejects identity points [3](#0-2) , so the empty-set path is the reachable one. `verify` is `#[must_use]` and generic, so an attacker-supplied `SchnorrAggregate` over an empty verifier-supplied key list is a forged signature that no `aggregator.complete()` could ever produce (`SchnorrAggregator` requires actual signatures).

### Impact Explanation
Any consumer that verifies an aggregate signature over a key/challenge set derived at runtime — which can be empty under edge conditions (e.g., zero eligible signers, all keys filtered out) — accepts `(0 || 0-scalar)` as a valid aggregate signature authorizing the action. This is precisely the Port3 pattern: a degenerate boundary input (empty/zero) bypasses the permission check the signature is meant to enforce. Severity is Medium: the forged signature requires the verifier to reach `verify` with an empty `keys_and_challenges`, which is a real but conditional reachability.

### Likelihood Explanation
`SchnorrAggregate::read` is explicitly one of the untrusted-byte entry points. A forgery needs only 36 bytes (`u32(0) || F::ZERO`), trivially constructible. The limiting factor is that exploitation requires a downstream caller whose `(key, challenge)` list can be empty while still treating a passing `verify` as authorization; within the indexed codebase the only consumers are in `coordinator/tributary` (out of scope), so real-world likelihood depends on integrator usage. Per crypto-library standards, a verifier that accepts a degenerate input is itself a defect regardless of caller.

### Recommendation
In `SchnorrAggregate::verify`, return `false` when `self.Rs.is_empty()` (and/or when `keys_and_challenges.is_empty()`), matching the explicit emptiness rejection already present in `check_keys` for MuSig (`MusigError::NoKeysProvided`) [4](#0-3) . Additionally, consider rejecting `len == 0` in `SchnorrAggregate::read` so the invalid encoding cannot enter the system.

### Proof of Concept
```rust
// crypto/schnorr, feature "aggregate"
let mut bytes = vec![0u8; 4]; // u32 len = 0
bytes.extend(<C as Ciphersuite>::F::ZERO.to_repr().as_ref());
let agg = SchnorrAggregate::<C>::read::<&[u8]>(&mut bytes.as_ref()).unwrap();
// Forgery: no signer, no nonce, no private key — verifies true
assert!(agg.verify(b"any dst", &[]));
```
The multiexp reduces to `(-0)*G == identity`, so `verify` returns `true` for an aggregate no honest `SchnorrAggregator` ever constructed.

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

**File:** crypto/frost/src/curve/mod.rs (L123-131)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```

**File:** crypto/dkg/musig/src/lib.rs (L46-63)
```rust
fn check_keys<C: Ciphersuite>(keys: &[C::G]) -> Result<u16, MusigError<C>> {
  if keys.is_empty() {
    Err(MusigError::NoKeysProvided)?;
  }

  let keys_len = u16::try_from(keys.len())
    .map_err(|_| MusigError::TooManyKeysProvided { max: u16::MAX, provided: keys.len() })?;

  let mut set = HashSet::with_capacity(keys.len());
  for key in keys {
    let bytes = key.to_bytes().as_ref().to_vec();
    if !set.insert(bytes) {
      Err(MusigError::DuplicatedParticipant(*key))?;
    }
  }

  Ok(keys_len)
}
```
