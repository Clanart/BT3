### Title
Crafted serialized `ThresholdKeys` is accepted without verifying `secret_share` matches `verification_shares[i]`, installing an attacker-defined group key - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes the DKG result (parameters, interpolation data, `secret_share`, and all `verification_shares`) from an untrusted byte stream and passes it to `ThresholdKeys::new`. `ThresholdKeys::new` checks counts and index bounds but never verifies that the supplied `secret_share` is consistent with `verification_shares[i]` (i.e., `C::generator() * secret_share == verification_shares[i]`), nor that the verification shares lie on a consistent polynomial. The group key is derived entirely from the attacker-controlled `verification_shares`. A crafted "key file" therefore produces an internally inconsistent, attacker-defined threshold wallet.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i`, an `Interpolation` variant, `secret_share`, and a `verification_shares` map of length `n`, all from the reader, then calls `ThresholdKeys::new` [1](#0-0) . Inside `ThresholdKeys::new`, the only checks are that `verification_shares.len() == n`, that no participant index exceeds `n`, and that `Interpolation::Constant` is only used when `t == n` [2](#0-1) . The group key is then computed by interpolating `verification_shares[1..=t]`, with no consistency check against `secret_share` [3](#0-2) .

Two independent crafted-blob attacks follow:

1. **Inconsistent share (brick the wallet / force invalid shares):** attacker supplies valid-looking `verification_shares` but a `secret_share` that does not satisfy `G * secret_share == verification_shares[i]`. Every signature share the node later produces via `view()`/`sign` fails verification-share checks, and the node has no way to detect this at load time — it is only discovered per-signature, when the node is blamed as faulty. Deposits made to the derived `group_key` are unspendable.
2. **Attacker-owned group key:** attacker supplies `verification_shares[j] = G * P(j)` for a polynomial `P` they fully know, plus the matching `secret_share = P(i)`. Deserialization succeeds, `group_key = G * P(0)` is a key whose discrete log the attacker knows. The node will then happily view/sign under a group key fully controlled by whoever supplied the file.

The analogous upstream flaw (crafted file upload executed/installed verbatim) maps onto Serai as crafted key material installed verbatim: the serialization format is treated as self-authenticating when it is not.

### Impact Explanation
`ThresholdKeys` is the root object consumed by FROST signing (`view()` returns the `ThresholdView` used for `sign`) and by the Bitcoin wallet layer for the group address. An attacker who can feed crafted bytes to `ThresholdKeys::read` either (a) silently bricks the threshold wallet so funds sent to `group_key` are unspendable, or (b) causes the node to operate under a group key the attacker controls, enabling unilateral signature forgery for that key and theft of any funds addressed to it. This is a critical-severity impact where the input surface is reachable; the honest-input serialization path provides no defense because `write`/`read` round-trip any field combination [4](#0-3) .

### Likelihood Explanation
Reachability is conditional: `ThresholdKeys::read` is used to restore keys from serialized bytes (e.g., on-disk restore or key material transported during recovery/resharing flows). The scope rules explicitly whitelist untrusted bytes fed to `ThresholdKeys::read`, and the PedPoP/recovery machinery moves key material between parties. Wherever the serialized blob is not exclusively produced by the local node's own prior `write`, the missing integrity check is exploitable. No cryptographic attack is needed — the attacker simply chooses the bytes. Likelihood is moderate; impact is high, so the finding rates High where the input path exists, Medium otherwise.

### Recommendation
In `ThresholdKeys::new` (so it is enforced for both `read` and programmatic construction), add:

- A consistency check that `C::generator() * secret_share == verification_shares[&params.i()]`, rejecting otherwise.
- Optionally, verify the `verification_shares` lie on a polynomial of degree `t - 1` consistent with the chosen `Interpolation` (for `Interpolation::Constant`, that the recorded coefficients reproduce all `verification_shares`; for `Lagrange`, this is inherent). At minimum, document and check that `group_key` derived from `1..=t` equals the interpolation over all `n` shares where determinable.
- Reject non-canonical or identity verification shares (note `read` uses `C::read_G` from `Ciphersuite`, which — unlike `Curve::read_G` — does **not** reject the identity point [5](#0-4) ; compare [6](#0-5) ).

### Proof of Concept
```rust
// Construct a serialized ThresholdKeys blob that deserializes successfully yet
// binds the node to an attacker-chosen group key (secret_share inconsistency variant).
// Layout per ThresholdKeys::read (crypto/dkg/src/lib.rs):
//   u32 id_len || C::ID || u16 t || u16 n || u16 i || u8 interpolation ||
//   F secret_share || G verification_shares[1..=n]

// t = 2, n = 3, i = 1, Lagrange interpolation (tag = 1)
// verification_shares[j] = G * P(j) for attacker-chosen P of degree 1:
//   P(x) = a0 + a1*x  =>  group_key = G * a0  (attacker knows a0)
// secret_share = arbitrary scalar s' != P(1)   // inconsistent variant

// bytes:
//   id_len = len(C::ID) as u32 LE
//   C::ID
//   02 00            // t = 2
//   03 00            // n = 3
//   01 00            // i = 1
//   01               // Interpolation::Lagrange
//   s'.to_repr()     // secret_share — does NOT satisfy G*s' == verification_shares[1]
//   (G*P(1)).to_bytes() || (G*P(2)).to_bytes() || (G*P(3)).to_bytes()

// ThresholdKeys::read(..) returns Ok.
// keys.group_key() == G * a0 — a key the attacker fully controls.
// Every share this node produces via keys.view(..)/sign fails verification
// against verification_shares[1], and funds sent to group_key are
// either attacker-spendable (consistent-share variant: s' = P(1))
// or unspendable (inconsistent variant).
```

Note on verification: I confirmed `ThresholdKeys::new` performs no `secret_share`↔`verification_shares` consistency check and that `read` uses `Ciphersuite::read_G` (no identity rejection). I did not fully trace how callers obtain the byte stream fed to `ThresholdKeys::read` in deployments; if the blob is always self-produced locally, reachability drops and this reduces to a hardening issue rather than a remotely exploitable flaw.

### Citations

**File:** crypto/dkg/src/lib.rs (L355-374)
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
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L538-561)
```rust
  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(&u32::try_from(C::ID.len()).unwrap().to_le_bytes())?;
    writer.write_all(C::ID)?;
    writer.write_all(&self.core.params.t.to_le_bytes())?;
    writer.write_all(&self.core.params.n.to_le_bytes())?;
    writer.write_all(&self.core.params.i.to_bytes())?;
    match &self.core.interpolation {
      Interpolation::Constant(c) => {
        writer.write_all(&[0])?;
        for c in c {
          writer.write_all(c.to_repr().as_ref())?;
        }
      }
      Interpolation::Lagrange => writer.write_all(&[1])?,
    };
    let mut share_bytes = self.core.secret_share.to_repr();
    writer.write_all(share_bytes.as_ref())?;
    share_bytes.as_mut().zeroize();
    for l in 1 ..= self.core.params.n {
      writer.write_all(
        self.core.verification_shares[&Participant::new(l).unwrap()].to_bytes().as_ref(),
      )?;
    }
    Ok(())
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

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```

**File:** crypto/frost/src/curve/mod.rs (L125-131)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```
