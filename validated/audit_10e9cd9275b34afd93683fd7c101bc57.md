### Title
Empty aggregate Schnorr signature forgeries accepted by `SchnorrAggregate::verify` - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` validates an aggregate signature by checking that `Rs.len() == keys_and_challenges.len()` and that a multiexponentiation over the weighted statements sums to identity. When both `Rs` and `keys_and_challenges` are empty, the length check passes (`0 == 0`), the statement list contains only `(-s, generator)`, and for `s = 0` the multiexp trivially evaluates to identity — so `verify` returns `true`. This mirrors the CSRF bug class (GHSA-q42q-523g-3fwv): a security check that can be bypassed when both sides of a compared/authenticated value are empty.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`:

- `SchnorrAggregate::read` (lines 77–88) accepts an attacker-supplied encoding with `len = 0` and `s = 0` — a 36-byte all-zero payload parses into `SchnorrAggregate { Rs: [], s: 0 }`.
- `SchnorrAggregate::verify` (lines 127–146) only enforces `self.Rs.len() == keys_and_challenges.len()`. With an empty aggregate and an empty key/challenge list, the loop appends no statements, `pairs` is just `[(-ZERO, generator)]`, and `multiexp_vartime(&pairs).is_identity()` is `true`.
- The aggregation side correctly refuses to produce an empty aggregate (`SchnorrAggregator::complete` returns `None` when `sigs.is_empty()`, lines 175–178), so no honest path can create this object — only deserialization of adversary bytes can. The verifier formula is therefore incomplete: it fails to reject the degenerate empty case that the prover side is explicitly designed never to emit.

The same empty-statement acceptance pattern exists in `DLEqProof::verify` / `MultiDLEqProof::verify` (`crypto/dleq/src/lib.rs` lines 160–180, 264–294): with empty `generators`/`points`, no statements are transcripted and an attacker can set `c` equal to the challenge over the bare transcript, producing a valid "proof" of nothing. The aggregate case is the stronger finding because the serialized form is attacker-controlled via `SchnorrAggregate::read`.

### Impact Explanation
Any unprivileged party can forge an aggregate Schnorr signature consisting of zero `Rs` and `s = 0` that `SchnorrAggregate::verify` accepts for an empty signer set. This is a forged signature accepted by the verifier formula: the verifier does not encode the invariant "at least one signer must have contributed", which the aggregation API (`complete` returning `None`) treats as part of correctness. Any caller that derives its signer list from attacker-influenced data (e.g., a bitmap that can be empty) and relies on `verify == true` to mean "the listed parties endorsed this message" is vulnerable to acceptance of an endorsement by nobody.

### Likelihood Explanation
Exploitation requires a caller that invokes `verify`/`read` on attacker-controlled bytes with a signer list that can be empty. The cryptographic primitive itself performs no non-emptiness check, so the bypass is deterministic — no probability or race is involved. Whether a reachable path exists depends on integrator behavior, but the verifier accepting a forgery it should reject is a defect regardless of caller discipline, matching the "empty header + empty cookie" bypass shape of the reference advisory.

### Recommendation
Reject the degenerate case in the verifier. In `SchnorrAggregate::verify`, return `false` when `self.Rs.is_empty()` (equivalently `keys_and_challenges.is_empty()`, since lengths are checked equal). Symmetrically, `DLEqProof::verify` and `MultiDLEqProof::verify` should error when `generators`/`points` are empty, so a proof over zero statements cannot be satisfied by a transcript-derived challenge.

### Proof of Concept
```rust
// crypto/schnorr: forge an aggregate signature for an empty signer set
// Encoding: u32 len = 0 || s = 0 (all-zero bytes)
let mut forged = vec![0u8; 4 + 32]; // len = 0, s = 0
let agg = SchnorrAggregate::<Ristretto>::read(&mut forged.as_slice()).unwrap();

// Caller verifies with an (attacker-influenced) empty signer list
let signers: Vec<(RistrettoPoint, Scalar)> = vec![];
assert!(agg.verify(b"some-dst", &signers)); // returns true — forged "aggregate"
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crypto/dleq/src/lib.rs (L160-180)
```rust
  pub fn verify<T: Transcript>(
    &self,
    transcript: &mut T,
    generators: &[G],
    points: &[G],
  ) -> Result<(), DLEqError> {
    if generators.len() != points.len() {
      Err(DLEqError::InvalidProof)?;
    }

    transcript.domain_separate(b"dleq");
    for (generator, point) in generators.iter().zip(points) {
      Self::verify_statement(transcript, *generator, *point, self.c, self.s);
    }

    if self.c != challenge(transcript) {
      Err(DLEqError::InvalidProof)?;
    }

    Ok(())
  }
```
