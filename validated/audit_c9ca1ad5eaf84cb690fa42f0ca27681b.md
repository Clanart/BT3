### Title
Missing secret-share/verification-share consistency check in `ThresholdKeys::new`/`ThresholdKeys::read` yields keys whose group key can never be signed for — funds deposited to it are stuck forever - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to the NFT `initialize` report — where the contract fails to validate that the configured category supplies can ever satisfy `maxMintSupply`, permanently trapping mint proceeds — `ThresholdKeys::new` fails to validate that the supplied `secret_share` is consistent with `verification_shares[params.i()]`. The `group_key` is computed purely from the first `t` verification shares, while the `secret_share` is stored unverified. `ThresholdKeys::read` accepts attacker-controlled bytes, runs them through `ThresholdParams::new` and `ThresholdKeys::new`, yet never checks `C::generator() * secret_share == verification_shares[i]`. The resulting `ThresholdKeys` is "successfully" constructed with a group key the holder cannot actually sign for.

### Finding Description
`ThresholdKeys::new` validates only the *count* of verification shares (`== n`), that each participant index is `<= n`, and that `Constant` interpolation is only used for `t == n`: [1](#0-0) 

It then derives `group_key` as the Lagrange/Constant-interpolated sum over `verification_shares[1..=t]` — completely independent of `secret_share`. No statement of the form `C::generator() * secret_share == verification_shares[&i]` (or any equivalent consistency check) exists anywhere in the constructor or in `read`.

`ThresholdKeys::read` then reconstructs keys from untrusted bytes: it reads `t`, `n`, `i`, the interpolation variant, an arbitrary `secret_share` scalar, and `n` arbitrary points as verification shares, and passes them straight to `ThresholdKeys::new`: [2](#0-1) 

The only place consistency is ever asserted is a `debug_assert_eq!(keys.group_key(), group_key)` in the trusted dealer path (`crypto/dkg/dealer/src/lib.rs:64`), which is compiled out in release and irrelevant for deserialized keys.

Downstream, `view()` interpolates `secret_share` and publishes `group_key()` derived from the verification shares. Any FROST signature attempt produces a share that fails verification against `verification_shares[i]` (or, worse, verifies against a group key unrelated to the secret), so the signing protocol aborts — the mint "never completes."

### Impact Explanation
**High** — the analog of "all mint fees stuck forever." A `ThresholdKeys` accepted by `read`/`new` advertises a `group_key()` that integrators (e.g., bitcoin-serai's `Scanner`/`p2tr_script_buf`) use as the deposit address. Deposits to that address are spendable only by signatures interpolating to the secret underlying `verification_shares[1..=t]`; since `secret_share` is unconstrained, no valid signing quorum exists. Funds received at `group_key` are permanently unspendable — satisfying the "funds reported received that are not spendable" acceptance criterion.

### Likelihood Explanation
**Medium** — mirroring the report's "users can easily misconfigure inputs." Any path feeding attacker-influenced or corrupt serialized bytes into `ThresholdKeys::read` (restored/copied key material, a tampered DB record, cross-version blobs) silently produces a structurally valid but cryptographically broken key set. The failure is latent: construction, `group_key()`, `serialize()` all succeed; only the first real signing attempt reveals the breakage, after funds may already have been received.

### Recommendation
In `ThresholdKeys::new` (hence also in `read`), add the consistency check:
```rust
if C::generator() * secret_share.deref() != verification_shares[&params.i()] {
  Err(DkgError::InvalidShare)?
}
```
For `Interpolation::Constant`, additionally validate `c.len() == n` (currently unchecked — `interpolation_factor` indexes `c[i-1]`, so a short vector panics rather than errors). Also reject `t > 1` verification-share sets whose interpolated `group_key` is the identity, matching the "sensible bounds" recommendation of the original report.

### Proof of Concept
Using `ThresholdKeys::<C>::read` (the untrusted-bytes entry point), serialize a well-formed blob where `secret_share` is `F::random()` but `verification_shares` are `G * s_i` for independent random `s_i` (or simply replace the secret-share field of an honestly generated serialization with a different scalar):

```rust
let mut buf = honestly_serialized_keys;           // valid ThresholdKeys encoding
// overwrite the secret_share field with a random scalar
buf[secret_share_offset..][..32].copy_from_slice(&C::F::random(rng).to_repr());
let keys = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap(); // succeeds
let deposit_addr = keys.group_key();              // advertised, funds sent here
keys.view(vec![Participant::new(1).unwrap(), ...]).unwrap();        // succeeds
// every FROST signing round fails share verification -> group_key unspendable
```

`read` returns `Ok`, `group_key()` returns a usable-looking point, yet no signature valid under that key can ever be produced — the exact "sum of category supplies < maxMintSupply" shape: two independently-consistent-looking parameters that are mutually inconsistent, undetected at initialization, with funds as the casualty.

### Citations

**File:** crypto/dkg/src/lib.rs (L355-378)
```rust
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L618-632)
```rust
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
  }
```
