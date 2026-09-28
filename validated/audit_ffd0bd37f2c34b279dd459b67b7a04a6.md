### Title
ThresholdKeys accepts an all-zero `Interpolation::Constant` coefficient vector, collapsing the group key to the identity (a publicly-known private key) - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes `Interpolation::Constant` coefficients via `C::read_F` with no non-zero/validity check, and `ThresholdKeys::new` only enforces `t == n` for `Constant` interpolation. An all-zero coefficient vector makes `group_key` an empty-weighted sum equal to the identity point, corresponding to secret key `0`. Any party can then forge signatures for that group key and steal funds sent to it — the direct analog of a zero `defaultScaledOfferFactor` silently pricing everything at zero.

### Finding Description
In `ThresholdKeys::new` (`crypto/dkg/src/lib.rs`), the only check applied to `Interpolation::Constant` is `params.t() != params.n()` — the coefficients themselves are never validated: [1](#0-0) . The group key is then computed as the interpolation-weighted sum of the verification shares: [2](#0-1) , where `interpolation_factor` simply returns `c[i - 1]`: [3](#0-2) . If every coefficient is `0`, `group_key` is the identity point regardless of the verification shares.

`ThresholdKeys::read` reads `n` coefficients straight from the byte stream with `C::read_F` and passes them unchecked into `ThresholdKeys::new`: [4](#0-3) . Unlike the analogous zero-guards elsewhere in the same file — `Participant::new` rejects `0`: [5](#0-4) , `ThresholdParams::new` rejects `t == 0`/`n == 0`: [6](#0-5) , and `ThresholdKeys::scale` rejects a zero scalar: [7](#0-6)  — there is no guard against a degenerate zero coefficient vector, nor any check that the resulting `group_key` is non-identity.

The view/signing path is consistent with this: `view` adds `offset` to `included[0]`'s share: [8](#0-7) , so even with the honest participants' shares the aggregate signing secret is `0 + offset`, a value fully determined by public data.

### Impact Explanation
A `ThresholdKeys` set whose `group_key` is the identity point has effective private key `0` (or exactly `offset`, which is public/ephemeral). Schnorr verification `s·G == R + H(R, A, m)·A` with `A = identity` reduces to `s·G == R`, so anyone can forge a valid signature for any message by picking `s` and setting `R = s·G`. Any bitcoin-serai wallet/output addressed to this group key is spendable by an unprivileged attacker — funds "received" under the misconfigured key are stealable, mirroring the oracle returning `quoteAmount = 0` for every price.

### Likelihood Explanation
The trigger is a deserialization/configuration path, not an exotic protocol interaction: `ThresholdKeys::read` is an explicitly untrusted input surface, and nothing in the byte format or constructor prevents encoding `n` zero field elements as the `Constant` coefficient list. An attacker who can supply or corrupt the serialized key blob (or an integrator that constructs `Interpolation::Constant(vec![F::ZERO; n])` by mistake, exactly like omitting oracle parameters) produces keys that silently "work" — `view`, `sign`, and `verify` all execute without error — while the group key is forgeable by everyone. No threshold collusion or malicious validator is required.

### Recommendation
In `ThresholdKeys::new`, when `interpolation` is `Interpolation::Constant`, reject any coefficient equal to zero (and/or require the computed `group_key` to be non-identity). Additionally, reject identity verification shares in `verification_shares`. This matches the existing zero-parameter defenses (`ThresholdParams::new`, `Participant::new`, `scale`) and prevents silent misconfiguration from collapsing the group's secret key to a publicly known value.

### Proof of Concept
```rust
// For any Ciphersuite C, craft a serialized ThresholdKeys blob:
//   id_len || C::ID || t=2 || n=2 || i=1 || interpolation tag 0 ||
//   F::ZERO || F::ZERO ||            // Constant coefficients, all zero
//   secret_share ||                  // arbitrary F
//   verification_share_1 || verification_share_2  // arbitrary valid G encodings
let keys = ThresholdKeys::<C>::read(&mut &blob[..]).unwrap();
// Succeeds: only t == n is checked for Constant interpolation.
assert_eq!(keys.params().t(), 2);
// group_key = sum_i vs_i * c[i-1] = identity since all c == 0
assert!(bool::from(keys.group_key().is_identity()));

// Anyone can now forge a Schnorr signature under this group key:
// pick s, set R = G * s; verification s*G == R + H(R, A, m)*A
// holds trivially since A == identity.
```

(No native PoC execution was performed; this is derived from `interpolation_factor`, `ThresholdKeys::new`'s `Constant` handling, and `ThresholdKeys::read`'s unchecked `read_F` loop in `crypto/dkg/src/lib.rs`.)

### Citations

**File:** crypto/dkg/src/lib.rs (L29-35)
```rust
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```

**File:** crypto/dkg/src/lib.rs (L166-169)
```rust
  pub const fn new(t: u16, n: u16, i: Participant) -> Result<ThresholdParams, DkgError> {
    if (t == 0) || (n == 0) {
      return Err(DkgError::ZeroParameter { t, n });
    }
```

**File:** crypto/dkg/src/lib.rs (L226-229)
```rust
  fn interpolation_factor(&self, i: Participant, included: &[Participant]) -> F {
    match self {
      Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)],
      Interpolation::Lagrange => {
```

**File:** crypto/dkg/src/lib.rs (L367-374)
```rust
    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L400-403)
```rust
  pub fn scale(mut self, scalar: C::F) -> Option<ThresholdKeys<C>> {
    if bool::from(scalar.is_zero()) {
      None?;
    }
```

**File:** crypto/dkg/src/lib.rs (L518-521)
```rust
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;
```

**File:** crypto/dkg/src/lib.rs (L604-616)
```rust
    let mut interpolation = [0];
    reader.read_exact(&mut interpolation)?;
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
      1 => Interpolation::Lagrange,
      _ => Err(io::Error::other("invalid interpolation method"))?,
    };
```
