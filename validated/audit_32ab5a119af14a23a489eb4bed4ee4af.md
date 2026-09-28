### Title
Malformed `ThresholdKeys` with identity verification shares crashes Bitcoin FROST signing - (File: `networks/bitcoin/src/crypto.rs`)

### Summary

`ThresholdKeys::read` accepts identity group points for all verification shares and `ThresholdKeys::new` derives an identity group key without rejecting it. When those keys are used with Bitcoin's BIP-340 FROST algorithm, `Algorithm::sign_share` passes the identity group key to `Hram::hram`, which calls `x` and panics on `expect("point at infinity")`. A party that can cause a node to deserialize attacker-controlled `ThresholdKeys` bytes can therefore crash the signing operation instead of receiving a deserialization or signing error.

### Finding Description

`ThresholdKeys::read` deserializes every verification share with `C::read_G`, then passes the resulting map to `ThresholdKeys::new`. [1](#0-0) 

`C::read_G` checks canonical encoding, but does not reject the canonical encoding of the group identity. [2](#0-1) 

`ThresholdKeys::new` computes `group_key` as a sum of interpolated verification shares and stores it without an identity check. [3](#0-2) 

During signing, `Schnorr::sign_share` computes `H::hram(&nonce_sums[0][0], &params.group_key(), msg)`. [4](#0-3) 

Bitcoin's `Hram::hram` calls `x(A)`, and `x` panics when `A` is the point at infinity. [5](#0-4) [6](#0-5) 

### Impact Explanation

An attacker can convert an otherwise recoverable malformed-input condition into a process panic. Any service that deserializes an attacker-supplied `ThresholdKeys<Secp256k1>` object and uses it with `bitcoin_serai::crypto::Schnorr` can be denied service when the object is first used to sign.

The identity group key is a semantically invalid key. Its encoding is still canonical, so the existing `read_G` validation does not reject it. The resulting object is accepted by `ThresholdKeys::read`, reaches `sign_share`, and panics inside Bitcoin's challenge routine.

### Likelihood Explanation

The trigger requires an unprivileged party to supply the bytes consumed by `ThresholdKeys::read` or otherwise influence key deserialization. The prompt's reachable-input model explicitly includes untrusted bytes passed to `ThresholdKeys::read`. Once such bytes are accepted, no threshold collusion, malformed peer message during signing, or secret knowledge is needed: all identity verification shares deterministically produce an identity group key, and signing deterministically reaches the panic.

### Recommendation

Reject identity points in semantically sensitive deserialization and key construction paths:

- In `ThresholdKeys::new`, reject an identity `group_key` and any identity verification shares.
- Alternatively, add a checked `read_G_nonzero`/subgroup-and-identity validation helper and use it for key material.
- As defense in depth, make `x` and `x_only` return `Option`/`Result` rather than panic, and propagate `FrostError::InvalidPreprocess` or `InternalError`.

### Proof of Concept

Construct a serialized `ThresholdKeys<Secp256k1>` whose:

1. Ciphersuite ID is `b"secp256k1"`.
2. `t = n = i = 1`.
3. Interpolation is `Constant([1])` or `Lagrange`.
4. Secret share is `1`.
5. The sole verification share is the canonical secp256k1 identity encoding.

Then deserialize and sign:

```rust
use std::io::Cursor;

use frost::{
  curve::Secp256k1,
  sign::{PreprocessMachine, SignMachine},
  Participant, ThresholdKeys,
};
use bitcoin_serai::crypto::Schnorr;
use rand_core::OsRng;

let keys = ThresholdKeys::<Secp256k1>::read(&mut Cursor::new(attacker_bytes))
  .expect("identity key material is accepted");

let machine = frost::sign::AlgorithmMachine::new(Schnorr::new(), keys);
let (machine, _preprocess) = machine.preprocess(&mut OsRng);

// The threshold is 1, so no remote preprocess is required.
// This reaches Hram::hram with params.group_key() == identity.
let _ = machine.sign(Default::default(), b"message");
```

The panic occurs at `encoded.x().expect("point at infinity")` inside `x`. [7](#0-6)

### Citations

**File:** crypto/dkg/src/lib.rs (L376-390)
```rust
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

**File:** crypto/frost/src/algorithm.rs (L201-210)
```rust
  fn sign_share(
    &mut self,
    params: &ThresholdView<C>,
    nonce_sums: &[Vec<C::G>],
    mut nonces: Vec<Zeroizing<C::F>>,
    msg: &[u8],
  ) -> C::F {
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
```

**File:** networks/bitcoin/src/crypto.rs (L10-16)
```rust
/// Get the x coordinate of a non-infinity point.
///
/// Panics on invalid input.
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

**File:** networks/bitcoin/src/crypto.rs (L59-67)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);
```
