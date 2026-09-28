### Title
Deserialization of `ThresholdKeys` produces internally-inconsistent key material (secret share not bound to verification shares / attacker-controlled group key) — (`crypto/dkg/src/lib.rs`)

### Summary
CVE-2017-7525 is a deserialization flaw where attacker-controlled bytes are turned into live objects whose internal invariants are never validated. The Serai analog is `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg`. Deserialization accepts attacker-supplied `secret_share`, `interpolation`, and `verification_shares` and computes `group_key` from the *claimed* verification shares, without ever checking that `C::generator() * secret_share == verification_shares[i]` or that the shares are consistent with the claimed polynomial. The result is a `ThresholdKeys` object that passes all constructors yet describes a group key the holder does not actually share.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, `i`, an interpolation tag, `secret_share`, and `n` verification shares, then calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` only validates the count and range of verification shares and that `t == n` for `Interpolation::Constant`; it then derives `group_key` by interpolating `verification_shares[1 ..= t]` [2](#0-1) . Nowhere is it checked that the deserialized `secret_share` corresponds to `verification_shares[params.i()]`, nor that the verification shares lie on a degree `t-1` polynomial consistent with anything. Analogous to the Jackson flaw, untrusted bytes instantiate an object that violates its semantic invariants, and every downstream consumer (`view()`, `group_key()`, FROST signing) treats it as trusted key material [3](#0-2) .

A crafted blob lets the supplier freely choose `group_key`: pick arbitrary scalars `s_1, …, s_t`, set `verification_shares[l] = G·s_l` for `l ∈ 1..=t` (with `Interpolation::Lagrange` and `t = n` or any valid params where the interpolation over `1..=t` yields `group_key = G · Σ λ_l s_l`, a key whose discrete log the crafter knows). The victim's `secret_share` and `verification_shares[i]` can simultaneously be set consistently (`G·s_i`) so that the victim's FROST signature shares verify, while the aggregate group key is fully known to the crafter. Note also `verification_shares` points are read via `Ciphersuite::read_G`, which does not reject the identity point [4](#0-3) , giving even more freedom in the malicious map.

### Impact Explanation
Any path where `ThresholdKeys` bytes come from an untrusted party (a supplied key package, imported backup, or peer-delivered blob) results in the node adopting a group key whose private key is known to the attacker. Downstream, addresses derived from `group_key()` (e.g., Bitcoin P2TR outputs the scanner credits as received) are spendable by the attacker alone — deposits credited to the victim's multisig are immediately stealable, matching the "funds reported received that are not spendable / unintended-key signing" acceptance criteria. Alternatively, inconsistent blobs cause persistent signing failures (DoS of the threshold wallet). Severity: High.

### Likelihood Explanation
Exploitation requires feeding crafted bytes to `ThresholdKeys::read`. It is a pure deserialization flaw requiring no cryptographic break, collusion, or malicious validator majority — only a delivery channel for a malicious serialized key blob. The missing check is unconditional and deterministic once the bytes are accepted.

### Recommendation
In `ThresholdKeys::new` (and thus `read`), verify:
1. `C::generator() * secret_share == verification_shares[params.i()]` — binds the secret share to the public material.
2. Reject identity verification shares (`is_identity`) to prevent degenerate groups.
3. Optionally verify the verification shares are pairwise consistent with a degree `t-1` polynomial (any `t+1` interpolation agrees), preventing mismatched-share key groups.

### Proof of Concept
```rust
// C = any Ciphersuite, e.g. Ristretto. Attacker crafts a blob for params t=2,n=2,i=1.
let s1 = C::random_nonzero_F(&mut rng); // attacker-known "share 1"
let s2 = C::random_nonzero_F(&mut rng); // attacker-known "share 2"
let evil_secret = s1; // victim told its share is s1

let mut blob = vec![];
blob.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
blob.extend(C::ID);
blob.extend(2u16.to_le_bytes()); // t
blob.extend(2u16.to_le_bytes()); // n
blob.extend(1u16.to_le_bytes()); // i
blob.push(1); // Interpolation::Lagrange
blob.extend(evil_secret.to_repr().as_ref());
blob.extend((C::generator() * s1).to_bytes().as_ref()); // verification_shares[1]
blob.extend((C::generator() * s2).to_bytes().as_ref()); // verification_shares[2]

let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();
// Succeeds: group_key = G * (s1*λ1 + s2*λ2), discrete log fully known to the attacker.
// The victim node will credit deposits to this "multisig" key it cannot defend.
```

### Citations

**File:** crypto/dkg/src/lib.rs (L355-379)
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

**File:** crypto/dkg/src/lib.rs (L445-447)
```rust
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
  }
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
