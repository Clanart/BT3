### Title
PedPoP share verifier ignores participant exponents, accepting shares evaluated at participant 1 - ([File: crypto/dkg/pedpop/src/lib.rs])

### Summary
`exponential` is intended to produce `(1, i, i², …)` coefficients for evaluating a sender's committed polynomial at the recipient's participant index. Its iterator discards the `fold` result and never assigns `exp * i`, so every coefficient remains `1`. Consequently, `share_verification_statements` verifies only `g·share == Σ commitments[j]`, regardless of the recipient index. A participant can submit a share equal to the sum of its committed polynomial coefficients and have it accepted as that participant's share even when the committed polynomial would evaluate to a different value.

### Finding Description
`polynomial` correctly evaluates coefficients at the recipient index when generating shares: it multiplies the accumulated share by the participant field element between coefficient additions. Verification is supposed to perform the same evaluation against the sender's public commitments.

However, `exponential` contains:

```rust
(0 .. values.len()).fold(C::F::ONE, |exp, l| {
  res.push((exp, values[l]));
  exp * i
});
```

The return value of `fold` is unused. Worse, `exp * i` computes the next exponent but does not assign it back to `exp`. Each iteration therefore pushes `(C::F::ONE, values[l])`.

`share_verification_statements` then sums those pairs and appends `-share·G`. It therefore verifies:

```text
share·G == A₀ + A₁ + … + Aₜ₋₁
```

instead of:

```text
share·G == A₀ + i·A₁ + … + i^(t-1)·Aₜ₋₁
```

The incorrect statement is queued from `calculate_share`, and the same incorrect `exponential` function is used to derive every participant's verification share from the commitment stripes.

### Impact Explanation
This is an incorrect verifier formula for DKG share validation. For recipient `i`, the verifier accepts only the polynomial's evaluation at `1`, not at `i`.

A sender can choose polynomial coefficients `a₀ … aₜ₋₁`, publish commitments `Aⱼ = aⱼ·G`, and send `s = Σaⱼ` to a participant `i != 1`. The malformed share is accepted because the verifier checks `s·G == ΣAⱼ`, even though the correct share is `Σaⱼiʲ`.

When such malformed shares are used through completion, the derived verification shares are also wrong: each participant's verification share is calculated from the same unweighted commitment sum rather than that participant's polynomial evaluation. This can produce threshold keys whose verification shares do not correspond to the intended shared polynomial and can collapse distinct per-participant shares into the same projected value. Valid shares for recipients other than participant `1` are also rejected, making the DKG fail under normal use.

### Likelihood Explanation
The malformed input is a public DKG message under the attacker's control. The sender only needs to choose coefficients and send their scalar sum as the encrypted share; no private data or noncanonical encoding is required. The flaw is deterministic and affects every PedPoP execution where verification relies on `share_verification_statements`.

The practical signing impact depends on the surrounding protocol completing with these malformed accepted shares. Even when it does not complete, this remains a direct violation of the PedPoP verification relation and causes honest shares to fail for most participant indexes.

### Recommendation
Correctly accumulate the exponent in `exponential`, for example:

```rust
fn exponential<C: Ciphersuite>(i: Participant, values: &[C::G]) -> Vec<(C::F, C::G)> {
  let i = C::F::from(u16::from(i).into());
  let mut res = Vec::with_capacity(values.len());
  let mut exp = C::F::ONE;
  for value in values {
    res.push((exp, *value));
    exp *= i;
  }
  res
}
```

Add regression tests asserting that `exponential(i, values)` produces coefficients `1, i, i², …`, and that a share equal to `Σaⱼ` is rejected for participant `i != 1` when `f(i) != f(1)`.

### Proof of Concept
Let a sender select coefficients:

```text
a₀ = 1
a₁ = 2
t = 2
```

It publishes commitments:

```text
A₀ = 1·G
A₁ = 2·G
```

For participant `i = 3`, the valid share is:

```text
f(3) = 1 + 2·3 = 7
```

The sender instead submits:

```text
s = a₀ + a₁ = 3
```

The intended verification relation is:

```text
s·G ?= A₀ + 3·A₁
3·G ?= 1·G + 6·G = 7·G
```

so it should fail.

Serai's `exponential` emits `(1, A₀)` and `(1, A₁)`, making `share_verification_statements` check:

```text
s·G ?= A₀ + A₁
3·G ?= 3·G
```

so the incorrect share is accepted for participant `3`.

Relevant code:

- Correct share generation evaluates the polynomial at the participant index: `crypto/dkg/src/../dkg/pedpop/src/lib.rs`, `polynomial`.
- Incorrect exponent generation leaves every coefficient as `1`: `crypto/dkg/pedpop/src/lib.rs`, `exponential`.
- The resulting statement is queued as the recipient's share verification in `calculate_share`.
- The same incorrect function derives verification shares after batch verification.