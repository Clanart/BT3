### Title
`ThresholdKeys::read` does not validate `secret_share`/`interpolation` consistency with `verification_shares`, letting attacker-crafted bytes define an arbitrary group key - ([File: crypto/dkg/src/lib.rs])

### Summary

The external report's bug class is a parameter that must be consistent with other parameters but is never cross-validated, so inconsistent internal state is committed and later produces incorrect/reverting outcomes. In Serai, `ThresholdKeys::read` (listed as an untrusted-bytes sink) deserializes `t`, `n`, `i`, the interpolation coefficients, the `secret_share`, and all `verification_shares` from raw bytes, then calls `ThresholdKeys::new`. `ThresholdKeys::new` validates counts and index bounds, but never validates that `secret_share * G == verification_shares[i]`, nor that the first `t` verification shares interpolate to any particular key. The group key is simply computed as `sum(verification_shares[i] * interpolation_factor(i))` for `i in 1..=t` — entirely from attacker-supplied points.

### Finding Description

In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` reads `n` group elements into `verification_shares` and a scalar `secret_share` with only structural checks (`Participant::new`, `ThresholdParams::new`, `t == n` for `Interpolation::Constant`) [1](#0-0) . `ThresholdKeys::new` then derives `group_key` purely from `verification_shares[1..=t]` and the interpolation factors, with no check that the secret share is consistent with `verification_shares[i]` [2](#0-1) . The FROST code itself acknowledges this hole: in `complete`, the fallback path comments that "the only known way to cause this ... is to deserialize a semantically invalid FrostKeys" [3](#0-2) .

This mirrors `_releaseIntervalSecs` being unvalidated against `_linearVestAmount`/`_endTimestamp - _startTimestamp`: here `secret_share` and `verification_shares` are unvalidated against each other, yet together they define the identity (group key) of the wallet.

### Impact Explanation

An attacker who can supply bytes to `ThresholdKeys::read` (e.g., a crafted/替换ed key blob consumed by an integrator) can set `verification_shares` to points with known discrete logs (`a_i * G`) and `Interpolation::Constant` coefficients of their choosing (with `t == n`). `group_key` becomes `sum(a_i * c_i * G)` — a key whose discrete log the attacker knows. The victim then operates on a `ThresholdKeys` whose `group_key()` reports this attacker-controlled key: any funds or outputs addressed to that group key are "received" under a key the honest secret share can never sign for — and which the attacker can sign for directly. This is "funds reported received that are not spendable" by the victim, and fully spendable by the attacker. Additionally, any signing attempt produces shares that fail `verify_share`/aggregate verification (inconsistent share vs. verification share), causing persistent signing failure — the analog of "last withdrawals revert because the calculation board doesn't match."

### Likelihood Explanation

Reachability is bounded by how `ThresholdKeys::read` is exposed: it parses fully attacker-controlled bytes, and the rules explicitly admit `ThresholdKeys::read` as an untrusted sink. Exploitation requires a deployment that loads threshold keys from an untrusted or corruptible source rather than freshly produced DKG output — plausible for key backup/restore or HSM-import flows, but not reachable through the normal in-protocol DKG path (PedPoP constructs keys internally). Hence Medium, matching the report's severity.

### Recommendation

In `ThresholdKeys::new` (or at the end of `read`), verify consistency: check `secret_share * C::generator() == verification_shares[params.i()]` (untweaked), and document/enforce that `verification_shares` must be a consistent sharing (optionally verify `group_key` against an expected value supplied by the caller, since a consistent-but-attacker-chosen share set still yields an attacker-known group key).

### Proof of Concept

```rust
// Conceptual: feed crafted bytes to ThresholdKeys::<Ed25519>::read
// ID: b"Ed25519" (with u32 len prefix)
// t = 2, n = 2, i = 1  (t == n so Interpolation::Constant is allowed)
let a1 = <Ed25519 as Ciphersuite>::F::random(&mut rng); // attacker-known
let a2 = <Ed25519 as Ciphersuite>::F::random(&mut rng);
// bytes: interpolation = 0 (Constant), coefficients c1 = 1, c2 = 0
// secret_share = arbitrary garbage s (does NOT equal a1)
// verification_shares = {1: a1*G, 2: a2*G}
let keys = ThresholdKeys::<Ed25519>::read(&mut &crafted[..]).unwrap();
// group_key = a1*1*G + a2*0*G = a1*G — attacker knows dlog = a1
assert_eq!(keys.group_key(), Ed25519::generator() * a1);
// Funds sent to group_key are spendable by the attacker (key = a1),
// while the victim's secret_share s cannot produce a valid share.
```

### Citations

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L604-631)
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

    let secret_share = Zeroizing::new(C::read_F(reader)?);

    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```

**File:** crypto/frost/src/sign.rs (L491-494)
```rust
    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
```
