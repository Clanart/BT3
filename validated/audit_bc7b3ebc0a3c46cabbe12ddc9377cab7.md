### Title
`ThresholdKeys` trusts the supplied quorum of verification shares without checking consistency, letting crafted keys report an unspendable group key - (File: crypto/dkg/src/lib.rs)

### Summary
The AFX incident's bug class — "the system trusted its declared validator set exactly as given, and the quorum said yes" — maps onto `ThresholdKeys::new` in `crypto/dkg/src/lib.rs`. The function accepts an arbitrary `HashMap<Participant, C::G>` of verification shares and computes `group_key` by interpolating only participants `1..=t`, with no check that the shares lie on a common degree `t-1` polynomial and no check that the holder's `secret_share` matches its own verification share. Crafted bytes fed to `ThresholdKeys::read` therefore produce keys whose reported `group_key()` does not correspond to any key the signing set can actually sign for.

### Finding Description
`ThresholdKeys::new` validates only the count and index bounds of `verification_shares` (`crypto/dkg/src/lib.rs:355-365`), then derives the group key exclusively from shares `1..=t` (`crypto/dkg/src/lib.rs:376-378`):

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

It never verifies:
- that `C::generator() * secret_share == verification_shares[i]` for the holder's own `i`,
- that the `n` verification shares are mutually consistent (i.e. interpolate to the same group key for every `t`-sized subset).

`ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) parses `t`, `n`, `i`, interpolation coefficients, the secret share, and all `n` verification shares directly from attacker-controllable bytes and passes them straight into `ThresholdKeys::new`. Like the AFX bridge contract that "asked whether enough validators agreed" without verifying the operators behind them, this constructor asks only whether enough shares are *present* — never whether they are *consistent*.

When `view()` is later invoked with an `included` set other than `1..=t` (`crypto/dkg/src/lib.rs:463-507`), Lagrange interpolation over the inconsistent shares yields a different effective group key than `group_key()` reported. `sign_share` then challenges with `params.group_key()` (the `1..=t` key, `crypto/frost/src/sign.rs:363`, `crypto/frost/src/algorithm.rs:208`) while the produced share verifies under the interpolant of the actual signing set — producing signatures invalid under the reported group key, and a reported group key that may correspond to no spendable secret at all.

### Impact Explanation
An unprivileged party who supplies serialized `ThresholdKeys` bytes (a key file, backup, or DKG output re-encoded via `ThresholdKeys::read`) can cause the victim to report a `group_key()` — and hence any deposit address derived from it — that the threshold set cannot spend from. Any signing set not exactly equal to `1..=t` interpolates to a different key, so funds sent to the reported group key are "received" but unspendable: exactly the "funds reported received that are not spendable" acceptance criterion. Medium severity: requires the victim to load attacker-influenced key material, but the public-input path (`ThresholdKeys::read`) is explicitly in scope and the failure is silent — no error is ever raised.

### Likelihood Explanation
`ThresholdKeys::read` is a public deserialization API intended for loading keys from storage or transport. Nothing in the format or the constructor detects an inconsistent share set, so any flow where key bytes are supplied, relayed, or restored by an untrusted party is exploitable. Detection is unlikely until signing is attempted or funds are already deposited.

### Recommendation
In `ThresholdKeys::new`, verify `C::generator() * *secret_share == verification_shares[&params.i()]` and, where feasible, check the `n` shares are consistent (e.g. verify that interpolation over additional subsets of size `t` yields the same group key, or require callers to pass the DKG-validated shares). At minimum, `read`/`new` should reject a share map whose interpolant depends on which `t` participants are chosen.

### Proof of Concept
Conceptually (Ristretto):

```rust
// n=3, t=2. Craft bytes for ThresholdKeys::read with:
// honest secret_share s_i and shares for participants 1,2 that interpolate to
// attacker-chosen key K = g*k. Share for participant 3 is set to an arbitrary
// point Y3 not on the polynomial through (1, Y1), (2, Y2).
let mut buf = vec![];
buf.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(2u16.to_le_bytes());          // t
buf.extend(3u16.to_le_bytes());          // n
buf.extend(1u16.to_le_bytes());          // i = participant 1
buf.push(1);                             // Lagrange
buf.extend(s1.to_repr().as_ref());       // secret share
for y in [y1, y2, y3_inconsistent] {
  buf.extend(y.to_bytes().as_ref());
}
let keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice()).unwrap();
// keys.group_key() == K (interpolated from shares 1..=t), accepted silently.
// Signing with included = {1,3} interpolates a different key K' != K, so the
// aggregate signature fails verification under K: funds at K are unspendable.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crypto/dkg/src/lib.rs (L376-379)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

```

**File:** crypto/dkg/src/lib.rs (L493-507)
```rust
    // The interpolation occurs multiplicatively, letting us scale by the scalar now
    let secret_share_scaled = Zeroizing::new(self.scalar * self.original_secret_share().deref());
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );

    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }
```

**File:** crypto/dkg/src/lib.rs (L574-632)
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
  }
```
