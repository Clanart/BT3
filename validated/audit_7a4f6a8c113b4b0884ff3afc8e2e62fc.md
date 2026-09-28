### Title
SchnorrAggregate::verify accepts an empty aggregate signature, letting a "signature over zero signers" pass verification - (File: crypto/schnorr/src/aggregate.rs)

### Summary
The Carapace finding is a vacuous-check bug: `_calculateLeverageRatio` returns 0 when `totalProtection == 0`, so the "leverageRatio below ceiling" gate passes and the pool transitions to Open with zero protection. The analog in Serai is in `crypto/schnorr/src/aggregate.rs`: `SchnorrAggregate::verify` treats a zero-element aggregate as trivially satisfiable — an aggregate carrying `Rs = []` and `s = 0` verifies successfully against an empty `keys_and_challenges` list, because the multiexp reduces to `-0·G == identity`. An unprivileged party can reach this purely through untrusted bytes via `SchnorrAggregate::read` (a 4-byte zero length followed by a zero scalar) fed to `verify`.

### Finding Description
`SchnorrAggregate::read` accepts a `u32` count of nonces with no lower bound:

- `crypto/schnorr/src/aggregate.rs:77-88` reads `len` then `len` points plus `s`; `len = 0` is accepted.

`verify` then only enforces length equality, not non-emptiness:

- `crypto/schnorr/src/aggregate.rs:127-145`: `if self.Rs.len() != keys_and_challenges.len() { return false }` — for `Rs.len() == 0` and an empty `keys_and_challenges`, this passes. The `pairs` vector then contains only `(-self.s, C::generator())`, and `multiexp_vartime(&pairs).is_identity()` is true whenever `s == 0` (and in fact the underlying multiexp on an empty set is identity by definition).

The honest path, `SchnorrAggregator::complete` (lines 175-178), explicitly refuses to produce an empty aggregate (`if self.sigs.is_empty() { return None }`), which proves the empty case is semantically invalid — a "signature" attesting that nobody signed. The verification side lacks the corresponding guard, mirroring the Carapace omission where the zero-denominator case was never rejected.

The reachable consumer is the Tributary/Tendermint coordinator (`coordinator/tributary/src/tendermint/mod.rs` references `SchnorrAggregate`), where aggregate signatures over validator commits are verified against a key/challenge list built from participating signers; an empty aggregate paired with an empty participation set produces `verify(...) == true` for an assertion no validator ever made.

### Impact Explanation
A forged proof/signature: bytes `00 00 00 00 || <32 zero bytes>` deserialize via `SchnorrAggregate::read` into an aggregate that `verify` accepts as valid for an empty signer set. Any logic that treats `SchnorrAggregate` verification as evidence that a set of parties signed a message/challenge will accept a signature that was never produced by anyone — the exact "zero denominator vacuously satisfies the check" class. Downstream (consensus vote aggregation, multisig attestations), this can mark a statement as endorsed by the group when no key holder participated.

### Likelihood Explanation
The bug is deterministic and requires no privileged position: the attacker only supplies serialized bytes to `SchnorrAggregate::read`/`verify`. Exploitability depends on a caller passing an empty `keys_and_challenges` (i.e., deriving the expected-signer set from attacker-controlled participation data rather than a fixed roster). If the verifier list is always non-empty and externally fixed, the length check rejects it — hence Medium rather than High, matching the Carapace severity.

### Recommendation
In `SchnorrAggregate::verify` (`crypto/schnorr/src/aggregate.rs`), reject the empty case explicitly, e.g. `if self.Rs.is_empty() || (self.Rs.len() != keys_and_challenges.len()) { return false }`. Optionally also enforce `len > 0` in `SchnorrAggregate::read` so the invalid form cannot even be constructed, matching `SchnorrAggregator::complete`'s `None` on empty input.

### Proof of Concept
```rust
// crypto/schnorr/src/aggregate.rs — conceptual PoC
// Deserialize an aggregate with zero nonces and s = 0
let mut bytes = vec![];
bytes.extend(0u32.to_le_bytes());               // len = 0
bytes.extend(Ed25519::F::ZERO.to_repr().as_ref()); // s = 0
let agg = SchnorrAggregate::<Ed25519>::read(&mut bytes.as_slice()).unwrap();
// No signer signed anything, yet:
assert!(agg.verify(b"dst", &[])); // returns true — forged acceptance
```
Equivalently, `multiexp_vartime` over `pairs = [(-0, G)]` yields the identity point, so the check at line 145 passes with zero backing signatures — the same "ratio is zero so the bound check passes" failure as the Carapace report.

Citations: [1](#0-0) [2](#0-1) [3](#0-2)

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
