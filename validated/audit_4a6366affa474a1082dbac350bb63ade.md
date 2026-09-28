### Title
`ThresholdKeys::read` deserializes a semantically inconsistent key share (no `secret_share * G == verification_shares[i]` check), enabling type-confusion-style object construction from attacker-controlled bytes - (File: crypto/dkg/src/lib.rs)

### Summary
The iccDEV bug class is deserialization producing an object whose dynamic contents don't match the type the caller assumes it has. The analog in Serai is `ThresholdKeys::read` / `ThresholdKeys::new`: the serialized form is a self-describing bundle of `(t, n, i)`, an interpolation tag (`0` = `Constant`, `1` = `Lagrange`), a `secret_share` scalar, and `n` verification-share points — yet nothing validates that the deserialized `secret_share` actually corresponds to `verification_shares[i]`, nor that the interpolated shares are consistent with each other. The reader constructs a `ThresholdKeys` that is structurally well-formed but semantically incoherent — the same class of failure as a type confusion, where bytes interpreted under one variant/layout yield an object that violates the invariants its consumers rely on.

### Finding Description
`ThresholdKeys::read` parses all fields from the byte stream [1](#0-0) , including a tag byte selecting `Interpolation::Constant(Vec<F>)` vs `Interpolation::Lagrange` [2](#0-1) . `ThresholdKeys::new` only validates share count, participant indexes, and that `Constant` implies `t == n`; it then derives `group_key` purely from the attacker-controlled `verification_shares` [3](#0-2) . There is no check that `C::generator() * secret_share == verification_shares[i]`. The downstream code itself acknowledges this gap: `complete()` comments that the "everyone had a valid share yet the signature was still invalid" state is reachable "to deserialize a semantically invalid FrostKeys" [4](#0-3) . The interpolation tag also changes the stream layout: flipping `0`→`1` on a Constant-serialized buffer causes the first coefficient to be parsed as `secret_share`, producing a coherent-but-wrong object rather than a rejection.

### Impact Explanation
Concrete impact matching the accepted criteria — funds received that are not spendable, and incorrect blame attribution. If a node loads `ThresholdKeys` from attacker-influenced bytes (key import/recovery/backup path, an enumerated reachable input), it adopts `group_key()` derived entirely from attacker-chosen verification shares. Funds sent to that group key are scanned/registered as belonging to the multisig, but the node's `secret_share` doesn't satisfy its own verification share: every FROST signing attempt either fails overall (`algorithm.verify` fails on the summed share) or pins blame on the honest node itself via `FrostError::InvalidShare(own_i)` [5](#0-4) . Deposits are credited to a key no honest quorum using these bytes can spend.

### Likelihood Explanation
Requires an attacker to control or tamper with the serialized `ThresholdKeys` a participant loads — the rules explicitly designate untrusted bytes to `ThresholdKeys::read` as reachable. Once loaded, the failure is deterministic (no probabilistic element): signing is guaranteed to fail or self-blame. Severity Medium: loss of availability of funds under the deserialized key and corrupted blame attribution, without direct secret extraction.

### Recommendation
In `ThresholdKeys::new` (or `ThresholdKeys::read` immediately after construction), verify `C::generator() * secret_share == verification_shares[&params.i()]` and reject otherwise. Optionally bind the interpolation tag check by requiring `Constant` coefficients to be consistent (`sum of c[i]*share[i]` relationship), so no tag byte flip produces a loadable-but-incoherent object.

### Proof of Concept
1. For `Secp256k1`/`Ristretto`, craft a serialized `ThresholdKeys` buffer: valid `C::ID`, `t = n = 2`, `i = 1`, tag `1` (Lagrange), `secret_share = a` (attacker-chosen scalar), `verification_shares = {1: a*G ... }` — or simply set `secret_share = x` and `verification_shares[1] = y*G` with `x != y`.
2. `ThresholdKeys::<C>::read(&mut bytes)` returns `Ok` — no consistency check at `lib.rs:625-630`.
3. `keys.group_key()` is a valid-looking public key; the system scans for outputs under it and credits deposits.
4. During FROST signing, `sign_share` uses `x`; the summed share fails `algorithm.verify`, then `verify_share` fails for participant `i`, returning `FrostError::InvalidShare(i)` — blaming the victim — and the deposited funds are unspendable.

Uncertainty note: the practical reachability hinges on integrators feeding untrusted bytes into `ThresholdKeys::read` (e.g., key import flows); the rules enumerate that input as in-scope, but I did not trace a specific in-repo caller that loads it from a network-controlled source.

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

**File:** crypto/dkg/src/lib.rs (L574-631)
```rust
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<ThresholdKeys<C>> {
    {
      let different = || io::Error::other("deserializing ThresholdKeys for another curve");

      let mut id_len = [0; 4];
      reader.read_exact(&mut id_len)?;
      if u32::try_from(C::ID.len()).unwrap().to_le_bytes() != id_len {
        Err(different())?;
      }

      let mut id = vec![0; C::ID.len()];
      reader.read_exact(&mut id)?;
      if id != C::ID {
        Err(different())?;
      }
    }

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

**File:** crypto/frost/src/sign.rs (L476-489)
```rust
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
