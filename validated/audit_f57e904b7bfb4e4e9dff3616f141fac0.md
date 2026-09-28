### Title
ThresholdKeys::read accepts attacker-chosen secret share and verification shares with no consistency check, enabling substitution of the effective group key - (File: crypto/dkg/src/lib.rs)

### Summary
The external report's bug class is "content validated under one criterion, then stored/interpreted under an independent, attacker-chosen criterion" (MIME sniffed from bytes, extension taken from the client). The analog in Serai is `ThresholdKeys::read` in `crypto/dkg/src/lib.rs`: every field is canonically validated in isolation (scalars via `read_F`, points via `read_G`, params via `ThresholdParams::new`), but no check binds the deserialized `secret_share` to `verification_shares[i]`, nor the claimed participant set to the shares actually serialized. `group_key` is derived solely from `verification_shares[1..=t]` in `ThresholdKeys::new`, so the declared secret share and the effective group key are fully independent — the same independence as content-vs-extension in the report.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) parses `t`, `n`, `i`, an interpolation tag, `n` interpolation coefficients or the Lagrange marker, one `secret_share` scalar, and `n` verification shares, then calls `ThresholdKeys::new`. `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) only checks:

- `verification_shares.len() == n` and all share indexes `<= n` (lines 355-365)
- `Constant` interpolation requires `t == n` (lines 367-374)
- computes `group_key = Σ_{i∈1..=t} verification_shares[i] * λ_i` (lines 376-378)

It never verifies `C::generator() * secret_share == verification_shares[params.i()]`. The code itself acknowledges this gap: `complete` in `crypto/frost/src/sign.rs` notes that reaching "everyone had a valid share yet the signature was still invalid" is only possible via "a semantically invalid FrostKeys" deserialization (crypto/frost/src/sign.rs:492-494).

An attacker who supplies the serialized bytes (backup/restore, key transport, or any path feeding untrusted input to `ThresholdKeys::read`, explicitly in scope) can craft:

- `t = n = i = 1` (or any params), `Interpolation::Lagrange`
- `secret_share` = an attacker-known scalar `x`
- `verification_shares[1]` = `G * x`, or more subtly an arbitrary point so `group_key` becomes an attacker-controlled key while `params` and curve ID look legitimate

The deserialized object passes all validation and is returned as a usable `ThresholdKeys<C>`.

### Impact Explanation
Two concrete consequences, both reachable from public/untrusted bytes:

1. **Key substitution on load.** Downstream code derives outputs from `group_key()` / `original_group_key()` — e.g. `tweak_keys` in `networks/bitcoin/src/wallet/mod.rs:46-75` hashes `keys.group_key()` into the TapTweak and `Scanner::new`/`register_offset` (wallet/mod.rs:162-196) builds the watched `script_pubkey` set from it. Substituting verification shares substitutes the wallet's address, so deposits are credited to an attacker-spendable key (funds "received" that the victim cannot spend, matching the report's stored-file-served-as-PHP shape: validated bytes interpreted under attacker-chosen semantics).
2. **Consensus/signing corruption.** With victim verification shares but a mismatched `secret_share`, the node emits signature shares that fail `verify_share`, causing deterministic signing failure or false `InvalidShare` blame attribution (crypto/frost/src/sign.rs:475-494) — an integrity violation with no cryptographic detection.

### Likelihood Explanation
Requires the attacker to control or influence the serialized `ThresholdKeys` bytes the victim reads (backup files, coordinator-distributed key blobs, RPC-fed restoration). This is a realistic medium-likelihood path: the format is explicitly a serialization API for untrusted transport, and all syntactic validation passes, so nothing else in the stack detects the substitution. The cryptographic-level consistency check (`G*share == share's verification share`) is standard in every DKG (`pedpop` performs exactly this via `share_verification_statements`, crypto/dkg/pedpop/src/lib.rs:487-491) but is skipped on deserialization.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), verify `C::generator() * *secret_share == verification_shares[&params.i()]` and reject otherwise. This is the deserialized-bytes analogue of PedPoP's share verification and removes the independent-metadata channel entirely.

### Proof of Concept
```rust
// Attacker-controlled bytes fed to ThresholdKeys::read (Secp256k1 shown)
let x = <Secp256k1 as Ciphersuite>::F::random(&mut OsRng); // attacker's scalar
let mut buf = vec![];
buf.extend(u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes());
buf.extend(Secp256k1::ID);
buf.extend(1u16.to_le_bytes()); // t = 1
buf.extend(1u16.to_le_bytes()); // n = 1
buf.extend(1u16.to_le_bytes()); // i = 1
buf.push(1);                    // Interpolation::Lagrange
buf.extend(x.to_repr().as_ref());                    // attacker-known secret_share
buf.extend((Secp256k1::generator() * x).to_bytes().as_ref()); // verification_shares[1]

// Succeeds: group_key() == G*x, an attacker-controlled key,
// yet params/ID are well-formed and all encodings are canonical.
let keys = ThresholdKeys::<Secp256k1>::read(&mut buf.as_slice()).unwrap();
assert_eq!(keys.group_key(), Secp256k1::generator() * x);
// tweak_keys(keys) / Scanner::new(keys.group_key()) now watch an attacker-owned address.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crypto/dkg/src/lib.rs (L349-391)
```rust
  pub fn new(
    params: ThresholdParams,
    interpolation: Interpolation<C::F>,
    secret_share: Zeroizing<C::F>,
    verification_shares: HashMap<Participant, C::G>,
  ) -> Result<ThresholdKeys<C>, DkgError> {
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

**File:** crypto/frost/src/sign.rs (L491-495)
```rust
    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
  }
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
