### Title
Attacker-controlled `ThresholdKeys` serialization substitutes group key and secret share, hijacking the validator's identity - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
Analogous to the OpenC3 self-XSS (attacker-supplied input reinterpreted as trusted content by the victim's own tooling), `ThresholdKeys::read` deserializes a fully self-describing key blob — curve ID, `t`, `n`, `i`, interpolation mode and coefficients, `secret_share`, and all `verification_shares` — and `ThresholdKeys::new` accepts it without ever checking that `secret_share` is consistent with `verification_shares[i]`, or that the resulting `group_key` matches the network's expected key. An attacker who can influence the bytes a validator imports (phishing/backup-restore/migration tooling, the direct analog of the report's "attacker influences the parameter input") makes the victim adopt a `ThresholdKeys` whose group private key is fully known to the attacker.

### Finding Description
`ThresholdKeys::read` reads every security-relevant field from the input stream: the interpolation tag byte selects `Interpolation::Constant` (reading `n` attacker-controlled scalars) or `Lagrange`; it then reads `secret_share` and `n` verification shares verbatim [1](#0-0) . It hands these to `ThresholdKeys::new`, which only checks the count and index bounds of `verification_shares`, requires `t == n` for `Constant`, and derives `group_key` by interpolating `verification_shares[1..=t]` — it never verifies `C::generator() * secret_share == verification_shares[i]` nor the group key against any external expectation [2](#0-1) . The docstring on `serialize` even notes the scalar/offset are excluded, i.e. the blob alone defines the identity [3](#0-2) . The test helper demonstrates this is exploitable in practice: `vectors_to_multisig_keys` fabricates a serialized blob with hand-chosen shares and `ThresholdKeys::read` happily imports it, producing a `group_key` of the attacker's construction [4](#0-3) .

### Impact Explanation
A crafted blob lets the attacker pick every verification share as `G * a_j` for known scalars `a_j`, so the resulting `group_key = G * known_scalar` (Lagrange factors sum to 1) has a discrete log known to the attacker, while the imported `secret_share` is also attacker-known. The victim node will then operate under this group key: any funds/tokens the protocol reports as received to that key are spendable by the attacker, and any FROST signature produced with the planted share is for a key the attacker controls. This is the cryptographic equivalent of the report's "script executes in the victim's authenticated session": attacker-influenced input replaces the security context, yielding theft of everything addressed to the imported key.

### Likelihood Explanation
Requires the attacker to influence the serialized bytes a node loads via `ThresholdKeys::read` (import/restore/migration path), mirroring the report's phishing precondition — hence Medium rather than higher. The rules explicitly list `ThresholdKeys::read` as an untrusted-byte sink. No collusion or malicious peer needed; a single crafted file suffices.

### Recommendation
On deserialization, verify `C::generator() * secret_share == verification_shares[i]` and reject otherwise; additionally bind the blob to an expected `group_key`/context out-of-band (e.g., have callers pass the expected public key and compare against the recomputed `group_key`) rather than letting the byte stream self-declare the validator's identity.

### Proof of Concept
1. Attacker picks scalar `a`, computes `A = G * a`, and builds a serialized `ThresholdKeys` blob for curve `C` with `t = n = i = 1`, interpolation tag `1` (Lagrange), `secret_share = a`, and one verification share `A` — exactly the layout produced by `write`/`serialize` [5](#0-4) .
2. The victim feeds these bytes to `ThresholdKeys::<C>::read`; `ThresholdKeys::new` succeeds because consistency of `secret_share` is never checked [6](#0-5) .
3. `keys.group_key()` returns `A`, whose discrete log `a` is known to the attacker. Any deposit the validator's stack attributes to `A` is spendable by the attacker — funds reported received that are not exclusively spendable by the victim.

### Citations

**File:** crypto/dkg/src/lib.rs (L355-390)
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
```

**File:** crypto/dkg/src/lib.rs (L565-571)
```rust
  ///
  /// This will not include the ephemeral scalar/offset.
  pub fn serialize(&self) -> Zeroizing<Vec<u8>> {
    let mut serialized = Zeroizing::new(vec![]);
    self.write::<Vec<u8>>(serialized.as_mut()).unwrap();
    serialized
  }
```

**File:** crypto/dkg/src/lib.rs (L591-623)
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
```

**File:** crypto/frost/src/tests/vectors.rs (L117-138)
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
    assert_eq!(these_keys.params().t(), vectors.threshold);
    assert_eq!(usize::from(these_keys.params().n()), shares.len());
    let participant = Participant::new(i).unwrap();
    assert_eq!(these_keys.params().i(), participant);
    assert_eq!(these_keys.original_secret_share().deref(), &shares[usize::from(i - 1)]);
    assert_eq!(hex::encode(these_keys.group_key().to_bytes().as_ref()), vectors.group_key);
    keys.insert(participant, these_keys);
```
