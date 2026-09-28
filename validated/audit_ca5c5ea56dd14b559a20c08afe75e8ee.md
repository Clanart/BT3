### Title
Empty `SchnorrAggregate` (zero `Rs`, `s = 0`) verifies as a valid signature for an empty signer set over any message — (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts a serialized aggregate containing zero nonces (`len == 0`) followed by `s = 0`, and `SchnorrAggregate::verify` accepts this aggregate as valid whenever the caller's `keys_and_challenges` list is also empty. The verification equation degenerates: with no signers, the multiexp reduces to `(-s)·G`, which equals the identity for `s = 0` regardless of the message, keys, or DST. An attacker can therefore fabricate a "valid" aggregate signature — 4 zero length bytes plus 32 zero bytes — for any message when the effective signer set is empty. This is a direct analog of CVE-2020-36478: an absent/degenerate input (zero aggregated signatures, like a NULL parameters entry) is treated identically to a validly-empty structure, and the object is considered valid where it should be rejected.

### Finding Description
`SchnorrAggregate::read` deserializes a `u32` count and that many points, then one scalar; `len = 0` is permitted and yields `Rs = []`: [1](#0-0) 

`verify` only checks `Rs.len() == keys_and_challenges.len()`, then queues `(z, Rs[i])`, `(z·c, key)` per signer and `(-s, G)`. With zero signers the statement list is just `(-s, C::generator())`, so `verify` returns `true` iff `s == 0`: [2](#0-1) 

Note the asymmetry with the aggregation side: `SchnorrAggregator::complete` explicitly refuses to produce an aggregate over zero signatures (`if self.sigs.is_empty() { return None; }`), i.e., the library's own invariant is that a valid aggregate must contain ≥1 signature. The deserialization/verification path does not enforce this invariant, so a forged byte string can express a state the honest code considers unrepresentable: [3](#0-2) 

Reachability: the 36 forged bytes are fed to `SchnorrAggregate::read`, which is one of the listed untrusted-input readers' sibling APIs and is used by consensus-facing aggregate verification (`verify_aggregate` reads the aggregate from attacker-influenced commit bytes and pairs it with a signer list). Any caller that computes or receives an empty `keys_and_challenges`/signer list — e.g., a commit or certificate naming zero validators — will accept the forged aggregate for an arbitrary `msg`, because `challenge(...)` per signer never executes and `msg` is never bound into the check.

### Impact Explanation
A forged signature attestation is accepted: bytes `[0u32 LE][32 zero bytes]` verify as a valid half-aggregate Schnorr signature over any message and any DST whenever the claimed signer set is empty. In a consumer where signer membership is itself derived from attacker-supplied data (e.g., a commit object carrying both the validator list and the aggregate signature), this can yield acceptance of an unsigned consensus attestation — a signature-validity bypass with integrity impact (CVSS-class: I:H analog), not merely a parsing quirk.

### Likelihood Explanation
Exploitation requires the verifier to evaluate the aggregate against an empty signer set. Honest aggregation can never emit such an aggregate (guarded in `complete`), so the forged state is only reachable via `read` on untrusted bytes. Whether a concrete deployed caller permits an empty signer list is the limiting factor; the crate-level verifier itself unconditionally accepts the forgery, and no defense (e.g., `if self.Rs.is_empty() { return false; }`) exists. Reachable via `SchnorrAggregate::read` on public input, consistent with the scan's untrusted-bytes criterion.

### Recommendation
Reject empty aggregates in both `SchnorrAggregate::read` (`if len == 0 { Err(..) }`, matching the `complete()` invariant) and defensively in `SchnorrAggregate::verify` (`if self.Rs.is_empty() { return false }`). This mirrors the NULL-parameters fix: an absent field must not be indistinguishable from a valid empty one.

### Proof of Concept
```rust
// crypto/schnorr — for any C: Ciphersuite, any msg/DST:
let forged: Vec<u8> = [0u32.to_le_bytes().as_ref(), &[0u8; 32]].concat();
let agg = SchnorrAggregate::<C>::read(&mut forged.as_slice()).unwrap();
assert_eq!(agg.Rs().len(), 0);
// Keys list derived from attacker data happens to be empty:
let keys_and_challenges: &[(C::G, C::F)] = &[];
// Verification reduces to (-0)·G == identity → true for ANY msg/DST
assert!(agg.verify(b"any-dst", keys_and_challenges));
```
Per-statement: `pairs = [(-0, G)]` → `multiexp_vartime` yields identity → `verify` returns `true`, while `SchnorrAggregator::new(dst).complete()` would have returned `None` — the forged state is unreachable through honest signing yet accepted by the verifier.

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

**File:** crypto/schnorr/src/aggregate.rs (L175-178)
```rust
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }
```
