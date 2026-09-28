### Title
`SchnorrAggregate::verify` accepts an empty aggregate (`Rs` length 0) as a valid signature - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregator::complete` refuses to emit an aggregate over zero signatures (returns `None`), but the corresponding verification path `SchnorrAggregate::verify` does not enforce that the aggregate is non-empty. An attacker-supplied byte stream parsed by `SchnorrAggregate::read` with `Rs` length 0 and `s == 0` passes verification, producing a forged "valid" aggregate signature attesting to nothing — the same class as `triggerEndEpoch` omitting the zero-TVL guard that `triggerDepeg` enforces: the production path guards the degenerate case, the acceptance path does not.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`, `SchnorrAggregator::complete` explicitly guards the zero-count case:

```rust
/// Complete aggregation, returning None if none were aggregated.
pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
  if self.sigs.is_empty() {
    return None;
  }
``` [1](#0-0) 

However `SchnorrAggregate::verify` only checks that `self.Rs.len() == keys_and_challenges.len()`: [2](#0-1) 

With `keys_and_challenges` empty and `Rs` empty, `pairs` reduces to the single term `(-self.s, C::generator())`. When `s == 0`, `multiexp_vartime` yields the identity and `verify` returns `true`. `SchnorrAggregate::read` imposes no lower bound on the `Rs` length prefix — it reads a `u32` and iterates — so an unprivileged party can craft the 5-byte payload `00 00 00 00` + 32 zero bytes, feed it to `SchnorrAggregate::read`, and obtain an object that verifies: [3](#0-2) 

This is reachable purely from untrusted bytes through the public `read`/`verify` API, matching the analog rule for `*::read` plus `verify`.

### Impact Explanation
Any caller that batch-attests events via aggregate Schnorr signatures (e.g., `coordinator/tributary` use of `SchnorrAggregate` in tendermint handling) can be given a forged aggregate that cryptographically "verifies" despite signing no challenges and binding no keys. The verifier's empty-case behavior contradicts the aggregator's own documented refusal to produce such an aggregate, so a forged artifact is accepted where the protocol intends none to exist — an unsigned-state acceptance analogous to an epoch ending with zero TVL that the sibling function would have reverted.

### Likelihood Explanation
The trigger requires only a verifier that calls `SchnorrAggregate::verify` with attacker-controlled bytes and a `keys_and_challenges` list that can be empty (or a caller that verifies the aggregate before confirming the expected signature count). No privileged position, collusion, or leaked key is needed — just the crafted serialization. The `verify` contract is `#[must_use]` and places the length-equality check as its only guard, so a caller following the API surface naturally hits the degenerate case.

### Recommendation
Mirror the producer-side guard in the verifier, in `crypto/schnorr/src/aggregate.rs` `SchnorrAggregate::verify`:

```rust
if self.Rs.is_empty() || (self.Rs.len() != keys_and_challenges.len()) {
  return false;
}
```

Optionally also reject `s == 0` aggregates, and add a defensive length floor in `SchnorrAggregate::read` (`len == 0` → `io::Error`) so deserialization itself refuses the non-producible encoding.

### Proof of Concept
```rust
use ciphersuite::Ristretto;
use schnorr::SchnorrAggregate;

// Attacker-controlled bytes: 0-length Rs vector, s = 0
let mut bytes = vec![0u8; 4 + 32];
let agg = SchnorrAggregate::<Ristretto>::read(&mut bytes.as_slice()).unwrap();
// keys_and_challenges empty -> pairs = [(0, G)] -> multiexp is identity
assert!(agg.verify(b"any_dst", &[]));
```
`complete()` can never produce this value (`self.sigs.is_empty()` → `None`), so any accepted verification of it is by definition a forged aggregate.

Note: I was unable to fully trace the `coordinator/tributary` call site to confirm whether an empty `keys_and_challenges` list is plausibly passed in production, since that crate is outside the listed in-scope directories; the vulnerability claim rests on the in-scope crypto API accepting a value its own producer refuses to emit.

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

**File:** crypto/schnorr/src/aggregate.rs (L175-178)
```rust
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }
```
