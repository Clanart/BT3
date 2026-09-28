### Title
`ThresholdKeys::write` emits a variable-length `Interpolation::Constant` coefficient list while `ThresholdKeys::read` consumes exactly `n` coefficients — serialization layout mismatch causes stream desync / misparsing (File: crypto/dkg/src/lib.rs)

### Summary
The external report describes a struct whose reserved region is smaller than the documented size, causing fields that follow it to be mislocated. The same class exists in `ThresholdKeys`'s serialization: the writer emits however many constant-interpolation coefficients the `Vec` happens to contain, while the reader unconditionally consumes exactly `n` `read_F` elements. Nothing validates that the two lengths agree, so any byte stream containing a serialized `ThresholdKeys` followed by other data (or a wrong-length `Constant` vector) parses the subsequent fields at the wrong offsets.

### Finding Description
`Interpolation::Constant(Vec<F>)` is stored with an unchecked length. In `ThresholdKeys::new` (crypto/dkg/src/lib.rs:367-374), the only validation for `Constant` is `params.t() == params.n()`; `c.len()` is never compared to `n` or `t`.

- `write` (crypto/dkg/src/lib.rs:544-550) writes tag `0` then iterates `c`, emitting `c.len()` coefficient encodings.
- `read` (crypto/dkg/src/lib.rs:606-613) reads tag `0` then consumes exactly `n` coefficients via `C::read_F`.

Consequences in both directions:

1. **Reader misparses attacker bytes.** `ThresholdKeys::read` is a public untrusted-bytes entry point. Since `n` is itself attacker-controlled (read from the stream at line 598), an attacker can craft a blob where the declared `n` differs from the number of coefficient encodings actually present, shifting `secret_share` and all `verification_shares` to attacker-chosen offsets within the stream. `read` never checks for trailing bytes or EOF consistency, so a misaligned but internally "valid" `ThresholdKeys` can be constructed whose `secret_share`/`verification_shares` were carved out of bytes the producer intended as other fields.
2. **Writer/reader asymmetry.** A `ThresholdKeys` built through the public constructor with `Constant(c)` where `c.len() != n` serializes `c.len()` coefficients; any peer re-reading those bytes consumes `n` and silently misinterprets the following `secret_share` and verification shares — a direct analog of the report's "reserved space doesn't match the layout, so subsequent fields collide."

### Impact Explanation
Medium. `ThresholdKeys::read` / `Commitments::read`-style functions are reached with untrusted bytes in DKG/PedPoP/promote flows. The length desync lets a byte stream producer create keys whose deserialized `secret_share` and `verification_shares` are drawn from shifted offsets — meaning the reconstructed `group_key` and share verification operate on wrong field elements rather than failing cleanly. At minimum this is a consensus/parsing collision between two correct implementations disagreeing on layout; at worst, shares validated against mislocated verification keys could pass `ThresholdKeys::new`'s checks while not corresponding to the intended polynomial, leading to signatures for an unintended group key.

### Likelihood Explanation
Reachable: `ThresholdKeys::read` accepts raw bytes and `n` is attacker-controlled, so the shifted-parse path is directly reachable. The write-side asymmetry requires someone to construct `Interpolation::Constant` with a non-`n` length — permitted by the public API since the length is unchecked. Severity is bounded because the misparse still produces canonically validated scalars/points, but the deserialization boundary disagreement is real and undocumented.

### Recommendation
In `ThresholdKeys::new`, reject `Interpolation::Constant(c)` when `c.len() != usize::from(params.n())`. In `write`, write an explicit coefficient-count prefix (or assert `c.len() == n`); in `read`, validate consumed length and reject trailing bytes before constructing keys.

### Proof of Concept
```rust
// Construct keys with Constant interpolation whose coefficient vec length
// differs from n — passes ThresholdKeys::new since only t == n is checked.
let keys = ThresholdKeys::<C>::new(
  ThresholdParams::new(t, n, i).unwrap(),
  Interpolation::Constant(vec![C::F::ONE; usize::from(n) + 1]), // n+1 coeffs
  secret_share,
  verification_shares, // n entries
).unwrap();

let mut bytes = keys.serialize().to_vec();
// write emitted n+1 coefficients; read consumes only n, so secret_share is
// parsed from the (n+1)-th coefficient's bytes and every verification share
// is shifted by one field element — silently, if the bytes happen to be
// canonical.
let back = ThresholdKeys::<C>::read(&mut bytes.as_slice()).unwrap();
// `back` shares no fields with `keys`, yet no error was raised.
```

Note: I verified the missing `c.len()` check in `ThresholdKeys::new` (lib.rs:367-374) and the asymmetric read/write loops directly. I did not fully trace every downstream caller feeding attacker bytes into `ThresholdKeys::read` within the in-scope crates, so the exact exploit chain (vs. parser-desync severity) is partially inferred.