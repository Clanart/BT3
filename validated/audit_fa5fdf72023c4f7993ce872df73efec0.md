### Title
`SchnorrAggregate::verify` accepts a vacuous aggregate with zero signers, letting an attacker forge an "authenticated" aggregate signature - (File: crypto/schnorr/src/aggregate.rs)

### Summary
Analogous to the Neo4j LDAP flaw (CWE-287, "any valid username with an arbitrary password is accepted"), `SchnorrAggregate::verify` in `crypto/schnorr/src/aggregate.rs` performs no check that at least one signer participated. An untrusted byte string decoding to `SchnorrAggregate { Rs: [], s: 0 }` passes verification unconditionally, because the multiexp reduces to a single `0 * G` term which is trivially the identity. This is reachable entirely through the public `SchnorrAggregate::read` + `verify` API with attacker-controlled bytes.

### Finding Description
`SchnorrAggregate::read` reads a `u32` count and then that many points; a count of `0` is accepted, and `s` is read via `C::read_F`, where `0` is a canonical scalar [1](#0-0) .

`verify` then only checks that `Rs.len() == keys_and_challenges.len()` — which is satisfied by `0 == 0` — and builds the verification equation `Σ z_i·R_i + z_i·c_i·A_i − s·G == 0`. With no signers the loop pushes nothing, leaving only `(-self.s, C::generator())`; with `s = 0` this is `0·G = identity`, so `multiexp_vartime(...).is_identity()` returns true [2](#0-1) .

Similarly, the aggregator-side path does not protect this: `SchnorrAggregator::complete` returns `None` for an empty set, so honest producers never emit an empty aggregate — but the verifier accepts one fabricated by an attacker anyway [3](#0-2) . The underlying signature equation itself is fine; the defect is the missing "at least one statement" access-control check, exactly like the Neo4j bug where a validly-formed request skipped the credential check.

### Impact Explanation
Any consumer that treats `SchnorrAggregate::verify` returning `true` as proof that a quorum signed a message can be bypassed: an attacker who supplies zero signers and the 4-byte payload `00 00 00 00 || serialize(0)` obtains a "valid" aggregate signature without any private key, nonce, or cooperation from a real signer. In a consensus/weight-based caller (e.g. the tendermint-style `verify_aggregate` usage pattern this type is built for), if signer-count/quorum is enforced only implicitly by verification succeeding, an empty aggregate authenticates as "everyone (nobody) signed". This is signature forgery — the strongest acceptance criterion.

### Likelihood Explanation
Exploitation requires only submitting crafted bytes to `SchnorrAggregate::read` followed by `verify` with an empty signer set — fully deterministic, no cryptographic work, no collusion, no compromised parties. Whether the empty-signer case is reachable end-to-end depends on the caller supplying an empty `signers`/`keys_and_challenges` list; the library itself places no guard, so any caller that derives the signer set from the attacker-supplied aggregate (or independently verifies an empty set) is vulnerable. Severity: High (authentication bypass with trivial exploit, but impact gated by caller-side quorum checks).

### Recommendation
In `SchnorrAggregate::verify`, return `false` when `keys_and_challenges.is_empty()` (equivalently when `self.Rs.is_empty()`), mirroring the existing `complete()` refusal to produce empty aggregates. Optionally also reject `self.s == 0` and identity `R` values at read time for defense-in-depth.

### Proof of Concept
```rust
// crypto/schnorr — for any C: Ciphersuite, e.g. dalek_ff_group::Ed25519
let mut bytes = vec![0u8; 4];            // u32 le len = 0
bytes.extend(C::F::ZERO.to_repr());      // s = 0
let agg = SchnorrAggregate::<C>::read(&mut bytes.as_slice()).unwrap();
// Attacker's aggregate verifies under an empty signer set:
assert!(agg.verify(b"any dst", &[]));
```
The loop at `verify` pushes no `(z·R, z·c·A)` pairs, the final pair is `(-0)·G`, and `multiexp_vartime` returns identity, so `verify` returns `true` — a forged aggregate signature accepted with no secret material, matching the Neo4j "valid request, no valid credential needed" access-control failure.

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

**File:** crypto/schnorr/src/aggregate.rs (L175-186)
```rust
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
