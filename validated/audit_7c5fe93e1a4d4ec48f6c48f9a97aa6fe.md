### Title
`ThresholdKeys::read` accepts semantically inconsistent key material — `secret_share` is never checked against `verification_shares[i]`, enabling unblamable signing-session sabotage — (File: crypto/dkg/src/lib.rs)

### Summary
The LangBot advisory (unrestricted file upload letting an attacker place arbitrary content where the system consumes it) maps onto Serai as *unrestricted deserialization*: `ThresholdKeys::read` reconstructs full signing state from raw bytes and `ThresholdKeys::new` performs only structural checks (share count, participant bounds, interpolation applicability). It never verifies the one relation that makes the structure coherent: `C::generator() * secret_share == verification_shares[params.i()]`.

### Finding Description
`ThresholdKeys::new` validates the quantity of verification shares, that every participant index `<= n`, and that `Constant` interpolation is only used when `t == n`. It then derives `group_key` solely from `verification_shares[1..=t]` via Lagrange interpolation [1](#0-0) . `secret_share` is stored verbatim with no consistency check [2](#0-1) . `ThresholdKeys::read` parses `t`, `n`, `i`, interpolation, `secret_share`, and all `verification_shares` from attacker-supplied bytes, then calls `ThresholdKeys::new` [3](#0-2) .

Because `group_key` is computed only from `verification_shares[1..=t]`, a crafted blob can:

- Keep `group_key` identical to the real multisig key by leaving shares `1..=t` untouched, while rewriting `secret_share` to an arbitrary scalar `g` and `verification_shares[i]` (for `i > t`) to `G * g`.
- Every subsequent FROST share the node emits verifies cleanly against `verification_shares[i]` during blame (`verify_share` uses `self.view.verification_share(*l)`), yet the aggregate `sum` is inconsistent with the real group key [4](#0-3) .
- The result is the code's own acknowledged "impossible" branch: `InternalError("everyone had a valid share yet the signature was still invalid")` — a signature session that fails with **no participant blamable** [5](#0-4) .

Alternatively, leaving `verification_shares` real but corrupting only `secret_share` causes the victim to emit an invalid share that *is* blamable — converting a corrupted/attacker-supplied key file into a false `FrostError::InvalidShare(victim)` attribution, which downstream protocol layers translate into `InvalidParticipant`/fault reports [6](#0-5) .

### Impact Explanation
An attacker who can cause a node to load crafted `ThresholdKeys` bytes (backup/restore paths, key-import tooling, any pipeline feeding untrusted bytes to `ThresholdKeys::read`) obtains either:

1. **Unblamable session sabotage**: signing sessions under the *correct* group key fail with `InternalError` while every individual share passes `verify_share`, defeating the blame/slashing mechanism and allowing indefinite, unattributed DoS of the threshold signing protocol.
2. **False incrimination**: the honest node is reported as the faulty participant (`InvalidShare(victim)`), which in a deployment maps to slashing or removal of a non-faulty validator.

The deserialization gains no validation of the semantic invariant that defines a valid key share, exactly paralleling the report's "storage of attacker-controlled content without restriction" class.

### Likelihood Explanation
Reachability is conditional on an integrator deserializing `ThresholdKeys` from bytes an attacker can influence (the rules expressly place `ThresholdKeys::read` in the attacker-reachable input surface). The library itself documents the failure mode — `InternalError` is reachable "only… [via] a semantically invalid FrostKeys" — confirming no consistency check exists. Exploitation requires no cryptography: the attacker constructs `V_i = G*g` for an arbitrary `g`. Severity: Medium (integrity of blame attribution; signing DoS), not Critical, since it cannot forge a signature under the real group key nor recover key material.

### Recommendation
In `ThresholdKeys::new` (or at least in `ThresholdKeys::read`), enforce `C::generator() * secret_share == verification_shares[&params.i()]`. Optionally also verify that the Lagrange combination of `verification_shares` over `1..=t` is non-identity. This makes deserialization self-validating and removes the "semantically invalid FrostKeys" state entirely.

### Proof of Concept
```rust
// Conceptual: craft a ThresholdKeys blob that preserves group_key
// but poisons participant i (i > t).
// 1. Take a valid serialized ThresholdKeys for params (t, n, i) with i > t.
// 2. Replace the secret_share field with g = random scalar.
// 3. Replace verification_shares[i] with G * g.
//    (verification_shares[1..=t] untouched => group_key unchanged)
// 4. ThresholdKeys::read succeeds: all structural checks pass.
// 5. In FROST sign(), the node emits share s_i = lambda_i*g + d_i + e_i*rho_i.
//    - algorithm.verify(group_key, Rs, sum) fails (sum inconsistent with group key).
//    - verify_share for i passes: s_i*G == lambda_i*(G*g) + D_i + rho_i*E_i.
//    - BatchVerifier finds no faulty share => InternalError, blame evaded.
// Variant: keep verification_shares real, corrupt only secret_share =>
// honest node blamed as FrostError::InvalidShare(i).
```

Root cause confirmed: `crypto/dkg/src/lib.rs` `ThresholdKeys::new` performs no `G * secret_share == verification_shares[i]` check, and `crypto/frost/src/sign.rs` `complete()` explicitly documents that this "semantically invalid FrostKeys" state is reachable only via deserialization.

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

**File:** crypto/dkg/src/lib.rs (L380-390)
```rust
    Ok(ThresholdKeys {
      core: Arc::new(Zeroizing::new(ThresholdCore {
        params,
        interpolation,
        secret_share,
        group_key,
        verification_shares,
      })),
      scalar: C::F::ONE,
      offset: C::F::ZERO,
    })
```

**File:** crypto/dkg/src/lib.rs (L591-631)
```rust
    let (t, n, i) = {
      let mut read_u16 = || -> io::Result<u16> {
        let mut value = [0; 2];
        reader.read_exact(&mut value)?;
        Ok(u16::from_le_bytes(value))
      };
      (
        read_u16()?,
        read_u16()?,
        Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?,
      )
    };

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

**File:** crypto/frost/src/sign.rs (L475-489)
```rust
    for l in self.view.included() {
      if let Ok(statements) = self.params.algorithm.verify_share(
        self.view.verification_share(*l),
        &self.B.bound(*l),
        responses[l],
      ) {
        batch.queue(&mut rng, *l, statements);
      } else {
        Err(FrostError::InvalidShare(*l))?;
      }
    }

    if let Err(l) = batch.verify_vartime_with_vartime_blame() {
      Err(FrostError::InvalidShare(l))?;
    }
```

**File:** crypto/frost/src/sign.rs (L491-494)
```rust
    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
```
