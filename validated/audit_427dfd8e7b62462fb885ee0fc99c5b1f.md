### Title
`ThresholdKeys::new` / `ThresholdKeys::read` never validates that `secret_share` corresponds to `verification_shares[i]` — attacker-supplied key blob yields a group key the holder cannot sign for - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to CVE-2023-36049 (untrusted input accepted and acted on without proper validation, CWE-20), `ThresholdKeys::read` accepts fully attacker-controlled bytes — `t`, `n`, `i`, interpolation coefficients, `secret_share`, and all `n` verification shares — and `ThresholdKeys::new` derives `group_key` purely from the supplied `verification_shares[1..=t]` without ever checking that the provided `secret_share` is consistent with `verification_shares[i]` (i.e., that `G * secret_share == verification_shares[i]`). Any relationship between the secret share and the published group key is taken on trust from the byte stream.

### Finding Description
`ThresholdKeys::read` deserializes `t`, `n`, `i`, an `Interpolation` (which for `Constant` reads `n` untrusted scalars), a `secret_share`, and `n` verification share points, then calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` only checks the count of shares, that share indexes are `<= n`, and that `Constant` interpolation implies `t == n`; it then computes `group_key` as the Lagrange/Constant-interpolated sum over `verification_shares` for participants `1..=t` [2](#0-1) . No equality check `C::generator() * secret_share == verification_shares[&i]` (or interpolation-consistent equivalent) is performed anywhere. Contrast with the honest construction path in PedPoP, where the secret share is accumulated from verified shares and `verification_shares[i]` is set to `C::generator() * self.secret` [3](#0-2)  — that consistency invariant is enforced by construction there, but silently assumed by the deserialization path.

### Impact Explanation
An unprivileged party able to supply the bytes fed to `ThresholdKeys::read` (e.g., a coordinator/peer distributing serialized key material, or any channel delivering threshold key blobs to a node) can craft a blob where `secret_share` is unrelated to the interpolation of `verification_shares`. The resulting `ThresholdKeys`:

- reports a `group_key()` the holder cannot actually sign for — outputs/funds addressed to that group key are "received" but unspendable by this participant;
- produces FROST signature shares that fail `verify` against the interpolated `verification_shares`, or worse, with `Constant` interpolation and crafted coefficients, shares that verify under a maliciously-shaped key relation while diverging from the group's real key — causing the honest node to be blamed as faulty or to participate in signing unintended key configurations.

This meets the accepted-impact bar of "funds reported received that are not spendable" and an incorrect key-derivation formula applied to untrusted input.

### Likelihood Explanation
Reachability is exactly the class accepted by the scope: untrusted bytes → `read_F` / `read_G` / `ThresholdKeys::read` → `ThresholdKeys::new`. The check that would reject the malformed input (`G * secret_share == verification_shares[i]`) is a single multiexp omitted entirely. Exploitation requires only delivering a crafted serialization; no collusion, no broken BFT, no leaked keys — the input-validation gap is wholly inside the in-scope `dkg` crate.

### Recommendation
In `ThresholdKeys::new` (covering both `read` and programmatic construction), after parameter validation, verify share consistency: check `C::generator() * secret_share == verification_shares[&params.i()]` before computing `group_key`, and return a `DkgError` on mismatch. Optionally, when `interpolation` is `Constant`, also confirm coefficient count equals `n` (already implied by read) and that shares' indexes are unique/non-zero via `Participant` typed keys rather than raw `u16`.

### Proof of Concept
Conceptual, for `C = Secp256k1`, `t = n = 2`, `i = 1`:

1. Attacker picks arbitrary `secret_share = s_att` and arbitrary points `V1, V2` interpolating to a group key `Y_att` unrelated to `s_att` (e.g., `V1 = G*x1`, `V2 = G*x2` with known logs so `Y_att` is even attacker-known, enabling the attacker — not the victim — to control the "group" key the victim reports).
2. Serialize: `ID_len || ID || t=2 || n=2 || i=1 || [0] || c1 || c2 || s_att || V1 || V2` (Constant coefficients satisfying `c1 + c2` shape per `interpolation_factor`) and deliver to victim's `ThresholdKeys::read`.
3. `read` succeeds; `params`, interpolation, secret share, and shares all parse. `ThresholdKeys::new` computes `group_key = V1*l1 + V2*l2` with no consistency check against `s_att`.
4. Victim calls `group_key()` → `Y_att`, reports/scans funds under `Y_att`. When signing, victim's share `s_att * interpolation_factor` verifies against `V1`-derived `verification_shares` only if the attacker arranged it — either the victim produces invalid shares (blamed as faulty) or signs for a key whose discrete log relationship the attacker chose. Funds at `Y_att` are unspendable by the honest threshold group.

### Citations

**File:** crypto/dkg/src/lib.rs (L355-391)
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
  }
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

**File:** crypto/dkg/pedpop/src/lib.rs (L512-521)
```rust
    for i in self.params.all_participant_indexes() {
      verification_shares.insert(
        i,
        if i == self.params.i() {
          C::generator() * self.secret.deref()
        } else {
          multiexp_vartime(&exponential::<C>(i, &stripes))
        },
      );
    }
```
