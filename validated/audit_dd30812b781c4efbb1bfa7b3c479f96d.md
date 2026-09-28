### Title
`ThresholdKeys::read` deserializes a secret share that is never checked against the verification shares, allowing an attacker-supplied key file to coerce a signer into producing signatures for a group key the attacker controls - (File: crypto/dkg/src/lib.rs)

### Summary
The FreedroidRPG bug class is "trusted parsing of attacker-modifiable serialized state leads to attacker-controlled behavior". Serai's analog is `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:573-632`), which reconstructs full signing key material from raw bytes — `t`, `n`, `i`, an `Interpolation` blob, a `secret_share` scalar, and `n` verification shares — yet neither `read` nor `ThresholdKeys::new` ever verifies that `secret_share` is consistent with `verification_shares[i]` (i.e. that `G * secret_share == verification_shares[i]`).

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i` (validated only by `ThresholdParams::new`'s range checks: nonzero, `t <= n`, `i <= n`), then reads an interpolation variant, a `secret_share` scalar via `C::read_F`, and `n` verification shares via `C::read_G`, then calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` checks only the count of verification shares, participant index ranges, and the `Constant`-vs-`t != n` rule, then computes `group_key` purely from `verification_shares[1..=t]` — the `secret_share` is stored unconditionally [2](#0-1) . There is no `debug_assert` or check equivalent to PedPoP's share-verification statements or the `debug_assert_eq!(our_pub_key, verification_shares[&params.i()])` present in `musig` [3](#0-2) . The deserialized `secret_share` is later used verbatim by the FROST signing path (`ThresholdView::secret_share`, `crypto/dkg/src/lib.rs:665`).

### Impact Explanation
An attacker who can supply the bytes fed to `ThresholdKeys::read` (e.g., a tampered keystore file, a backed-up/migrated key blob, or any transport carrying serialized `ThresholdKeys`) can craft a blob where:

- `verification_shares` are chosen so the resulting `group_key` is a key the attacker fully knows the discrete log of, and
- `secret_share` is set to the attacker's own scalar.

The victim then runs FROST signing under `params.i()` producing signature shares that combine into valid signatures for the attacker-controlled group key — i.e., the node acts as a signing oracle for a key it never generated. Conversely, setting `secret_share` inconsistent with `verification_shares[i]` while keeping an honest `group_key` yields signature shares that either fail verification or, in share-reuse/ROS-style contexts, leak a share that does not match the committed verification share, corrupting blame attribution. This is a forged/injected-key primitive: concrete signing under an unintended key, reachable purely through untrusted bytes to `ThresholdKeys::read`.

### Likelihood Explanation
`ThresholdKeys::serialize`/`read` exists precisely so key material can be persisted and transported; any pipeline where these bytes pass through attacker-influenceable storage (shared volumes, backup restoration, DB rows written by another component, coordinator relays) exposes the path. The missing check is a single missing comparison — `read` already performs canonical scalar/point decoding, so the only gap is the semantic consistency check. Likelihood is moderate: it requires write access to the serialized blob, but no threshold cooperation, no collusion, and no protocol participation.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), reject inputs where `C::generator() * secret_share != verification_shares[&params.i()]`, analogous to the `debug_assert` in `musig` but enforced. Additionally consider binding the serialized blob to its context (curve ID is already checked; extend to a version/domain tag) so cross-protocol blobs cannot be substituted.

### Proof of Concept
```rust
// Attacker-controlled blob construction (same format as ThresholdKeys::write)
let attacker_secret = <C as Ciphersuite>::F::random(&mut rng);
let mut blob = vec![];
blob.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
blob.extend(C::ID);
blob.extend(1u16.to_le_bytes()); // t = 1
blob.extend(1u16.to_le_bytes()); // n = 1
blob.extend(1u16.to_le_bytes()); // i = 1
blob.push(1);                    // Interpolation::Lagrange
blob.extend(attacker_secret.to_repr().as_ref()); // secret_share
// verification_shares[1] = attacker pubkey => group_key = attacker pubkey
blob.extend((C::generator() * attacker_secret).to_bytes().as_ref());

// Victim loads attacker-chosen key material without error
let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();
assert_eq!(keys.group_key(), C::generator() * attacker_secret); // attacker-controlled key
// keys now signs under a group key whose discrete log the attacker knows
```
The load succeeds because `ThresholdKeys::new` performs no `secret_share`/verification-share consistency check [4](#0-3) ; the test helper `vectors_to_multisig_keys` demonstrates the identical serialization layout being consumed by `read` [5](#0-4) .

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

**File:** crypto/dkg/src/lib.rs (L591-632)
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
  }
```

**File:** crypto/dkg/musig/src/lib.rs (L151-161)
```rust
  let group_key = multiexp::multiexp(&multiexp);
  debug_assert_eq!(our_pub_key, verification_shares[&params.i()]);
  debug_assert_eq!(musig_key_vartime::<C>(context, keys), Ok(group_key));

  ThresholdKeys::new(
    params,
    Interpolation::Constant(binding_factors),
    private_key,
    verification_shares,
  )
  .map_err(MusigError::DkgError)
```

**File:** crypto/frost/src/tests/vectors.rs (L117-131)
```rust
  for i in 1 ..= u16::try_from(shares.len()).unwrap() {
    // Manually re-implement the serialization for ThresholdKeys to import this data
    let mut serialized = vec![];
    serialized.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
    serialized.extend(C::ID);
    serialized.extend(vectors.threshold.to_le_bytes());
    serialized.extend(u16::try_from(shares.len()).unwrap().to_le_bytes());
    serialized.extend(i.to_le_bytes());
    serialized.push(1);
    serialized.extend(shares[usize::from(i) - 1].to_repr().as_ref());
    for share in &verification_shares {
      serialized.extend(share.to_bytes().as_ref());
    }

    let these_keys = ThresholdKeys::<C>::read::<&[u8]>(&mut serialized.as_ref()).unwrap();
```
