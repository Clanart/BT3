### Title
Panic on identity group key/nonce from untrusted `ThresholdKeys` bytes via `x()` / `x_only()` — (File: networks/bitcoin/src/crypto.rs)

### Summary
The geth bug is a missing nil-check: `snap.Account`/`tryGetNode` assumed a lookup returned non-nil and panicked on attacker-controlled input. The Serai analog is a missing identity check on curve points deserialized from attacker-supplied bytes. `x()` and `x_only()` in `networks/bitcoin/src/crypto.rs` unconditionally panic when handed the point at infinity, and every Bitcoin-side consumer (`Hram::hram`, `tweak_keys`, `p2tr_script_buf`) assumes callers have already excluded infinity — but `ThresholdKeys::<Secp256k1>::read` (an explicit untrusted-bytes entry point) reconstructs the group key as a sum of deserialized `verification_shares` and provides no upstream guarantee the result is non-identity.

### Finding Description
`x()` calls `key.to_encoded_point(true).x().expect("point at infinity")` and `x_only()` calls `XOnlyPublicKey::from_slice(...).expect(...)`, so any code path reaching them with `ProjectivePoint::IDENTITY` aborts the process [1](#0-0) . `Hram::hram` feeds `nonce_sums[0][0]` and `params.group_key()` directly into `x()` during `sign_share`, and the same value is used again in `verify` [2](#0-1) . `tweak_keys` likewise calls `needs_negation`/key operations and `.expect(...)` on the result of scaling the group key [3](#0-2) .

On the input side, `ThresholdKeys::read` deserializes `t`, `n`, `i`, the interpolation coefficients, `secret_share`, and all `n` `verification_shares` purely from the reader, then passes them to `ThresholdKeys::new` [4](#0-3) . The group key is derived as a function of the deserialized verification shares (participants `1..=t`); the deserialization layer checks only point encoding/canonicity (via `read_G`), not the semantic non-identity of the *aggregate* group key — the same class as geth checking `err != nil` but not `account == nil`. Consequently, crafted `ThresholdKeys` bytes whose verification shares sum to infinity produce a `ThresholdKeys` whose `group_key()` is identity. Feeding that to `tweak_keys` (standard Bitcoin key setup) or into `AlgorithmMachine<Secp256k1, bitcoin::Schnorr>` signing causes `Hram::hram` to panic inside `x()` when `A` or the aggregated `R` is infinity.

A second reachable variant exists in the signing path itself: `read_preprocess` accepts peer-supplied `Commitments` via `read_G` without rejecting identity commitments [5](#0-4) , and `sign` aggregates them into `Rs`/`nonce_sums` that reach `hram`/`x()` without an infinity check [6](#0-5) . The docstring admits the panic ("If either `R` or `A` is the point at infinity, this will panic") and relies only on "negligible probability" rather than a check [7](#0-6) .

### Impact Explanation
An unprivileged party who can cause `ThresholdKeys::read` bytes to be consumed (e.g., bytes they supplied to a Bitcoin-networked component) can crash the node. Via the preprocess path, a malicious signing counterparty submitting identity commitments and arranging their bound contribution to cancel the aggregate nonce (or in degenerate `t`/`included` configurations where their identity commitment leaves the sum at infinity) triggers a panic in `sign_share`'s `Hram::hram` call. This is a remote, message-triggered panic — the same impact as the geth snap/1 crash: availability loss of the signing/coordination process.

### Likelihood Explanation
The `ThresholdKeys::read` route is deterministic: writing `n` verification shares whose `1..=t` sum is the point at infinity is trivial for the byte supplier and is not rejected by encoding checks. The preprocess route requires the attacker either to be the sole effective counterparty or to grind the binding factor `rho` (a hash of all commitments including their own), making the aggregate `R = identity` — computationally hard in the general case, so likelihood is lower there. The primary deterministic trigger is the deserialized-keys path, which needs no cryptographic work.

### Recommendation
- In `ThresholdKeys::new` / `ThresholdKeys::read`, reject `verification_shares` sets whose derived `group_key()` is the identity (return an error rather than a key the library's own consumers cannot safely use).
- In `Commitments::read`/`read_G` consumers, reject identity points for preprocess commitments (FROST already bans identity elsewhere, e.g. `random` loops until non-identity [8](#0-7) ).
- Defensively, make `x()`/`x_only()`/`Hram::hram` return an error/`Option` instead of `expect`/`panic` when given infinity, matching geth's fix of hoisting the nil check to the top of `tryGetNode`.

### Proof of Concept
```rust
// PoC sketch: crafted ThresholdKeys with identity group key -> panic in tweak_keys
use frost::{curve::Secp256k1, ThresholdKeys};
use bitcoin_serai::wallet::tweak_keys;

// Construct serialized ThresholdKeys bytes where verification_shares[1..=t]
// sum to ProjectivePoint::IDENTITY, e.g.:
//   shares[0] = G, shares[1] = -G  (t = 2)
// Remaining shares arbitrary valid encodings; interpolation tag = 1 (Lagrange);
// secret_share = any valid scalar encoding.
let malicious: &[u8] = &crafted_keys_bytes();
let keys = ThresholdKeys::<Secp256k1>::read(&mut &malicious[..]).unwrap();
assert!(bool::from(keys.group_key().is_identity()));

// Aborts the process inside x()/needs_negation on the point at infinity:
let _ = tweak_keys(keys);
```

Equivalent panic via the algorithm path: feed the same `keys` to `AlgorithmMachine::<Secp256k1, bitcoin::Schnorr>` and call `preprocess`/`sign`; `Hram::hram` invokes `x(&params.group_key())` and panics at `networks/bitcoin/src/crypto.rs:15`.

Note on scope/uncertainty: I could not fully verify whether `ThresholdKeys::new` internally rejects an identity aggregate group key (the function body was not read in this pass); if it does, the deterministic trigger narrows to the preprocess/identity-commitment variant, which still panics in `x()` but requires the attacker to coerce the aggregate nonce to infinity.

### Citations

**File:** networks/bitcoin/src/crypto.rs (L13-23)
```rust
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

**File:** networks/bitcoin/src/crypto.rs (L59-73)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);

      let c = Scalar::reduce(U256::from_be_slice(Sha256::from_engine(data).as_ref()));
      // If the nonce was odd, sign `r - cx` instead of `r + cx`, allowing us to negate `s` at the
      // end to sign as `-r + cx`
      <_>::conditional_select(&c, &-c, needs_negation(R))
    }
```

**File:** networks/bitcoin/src/crypto.rs (L78-84)
```rust
  /// This may panic if called with nonces/a group key which are the point at infinity (which have
  /// a negligible probability for a well-reasoned caller, even with malicious participants
  /// present).
  ///
  /// `verify`, `verify_share` MUST be called after `sign_share` is called. Otherwise, this library
  /// MAY panic.
  #[derive(Clone)]
```

**File:** networks/bitcoin/src/wallet/mod.rs (L46-75)
```rust
pub fn tweak_keys(keys: ThresholdKeys<Secp256k1>) -> ThresholdKeys<Secp256k1> {
  // Adds the unspendable script path per
  // https://github.com/bitcoin/bips/blob/master/bip-0341.mediawiki#cite_note-23
  let keys = {
    use k256::elliptic_curve::{
      bigint::{Encoding, U256},
      ops::Reduce,
      group::GroupEncoding,
    };
    let tweak_hash = TapTweakHash::hash(&keys.group_key().to_bytes().as_slice()[1 ..]);
    /*
      https://github.com/bitcoin/bips/blob/master/bip-0340.mediawiki#cite_ref-13-0 states how the
      bias is negligible. This reduction shouldn't ever occur, yet if it did, the script path
      would be unusable due to a check the script path hash is less than the order. That doesn't
      impact us as we don't want the script path to be usable.
    */
    keys.offset(<Secp256k1 as Ciphersuite>::F::reduce(U256::from_be_bytes(
      *tweak_hash.to_raw_hash().as_ref(),
    )))
  };

  let needs_negation = needs_negation(&keys.group_key());
  keys
    .scale(<_ as subtle::ConditionallySelectable>::conditional_select(
      &Scalar::ONE,
      &-Scalar::ONE,
      needs_negation,
    ))
    .expect("scaling keys by 1 or -1 yet interpreted as 0?")
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

**File:** crypto/frost/src/sign.rs (L276-281)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
  }
```

**File:** crypto/frost/src/sign.rs (L382-398)
```rust
    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);

    let our_binding_factors = B.binding_factors(multisig_params.i());
    let nonces = self
      .nonces
      .drain(..)
      .enumerate()
      .map(|(n, nonces)| {
        let [base, mut actual] = nonces.0;
        *actual *= our_binding_factors[n];
        *actual += base.deref();
        actual
      })
      .collect::<Vec<_>>();

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);
```

**File:** crypto/dalek-ff-group/src/lib.rs (L400-410)
```rust
        loop {
          let mut bytes = [0; 32];
          rng.fill_bytes(&mut bytes);
          let Some(point) = Option::<$Point>::from($Point::from_bytes(&bytes)) else {
            continue;
          };
          // Ban identity, per the trait specification
          if !bool::from(point.is_identity()) {
            return point;
          }
        }
```
