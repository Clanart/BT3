### Title
`ThresholdKeys::new`/`ThresholdKeys::read` do not enforce consistency invariants between `secret_share`, `verification_shares`, and the derived `group_key` - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
Like `_setStoppedValidatorCounts`, `ThresholdKeys::new` (and its untrusted-byte entry point `ThresholdKeys::read`) accepts externally supplied values and derives a security-critical aggregate (the group key) without enforcing the invariants that (a) `generator() * secret_share == verification_shares[params.i()]` and (b) every verification share — including indexes `> t` — is consistent with that same group key. The group key is computed solely from `verification_shares[1..=t]` at `crypto/dkg/src/lib.rs:376-378`, leaving shares at indexes `t+1..=n` completely unconstrained relative to the reported group key, and the local `secret_share` is never checked against its own verification share (`crypto/dkg/src/lib.rs:349-390`, `crypto/dkg/src/lib.rs:574-632`).

### Finding Description
`ThresholdKeys::read` deserializes `t`, `n`, `i`, the interpolation coefficients, the `secret_share`, and `n` verification shares from raw bytes, then calls `ThresholdKeys::new`. `new` validates only:

- `verification_shares.len() == n` (`crypto/dkg/src/lib.rs:355-360`)
- participant indexes `<= n` (`crypto/dkg/src/lib.rs:361-365`)
- `Constant` interpolation implies `t == n` (`crypto/dkg/src/lib.rs:367-374`)

It then computes `group_key = Σ_{i∈1..=t} verification_shares[i] * interpolation_factor(i, {1..=t})` (`crypto/dkg/src/lib.rs:376-378`). Two invariants are missing:

1. **Share↔key binding:** nothing checks `C::generator() * secret_share == verification_shares[params.i()]`. A crafted byte stream can pair any secret scalar with any verification share.
2. **Full-domain binding:** `verification_shares[t+1..=n]` never influence `group_key`. In `view()`, however, interpolation is performed over the actual `included` set (`crypto/dkg/src/lib.rs:494-507`), so shares beyond index `t` do determine the per-subset signing relation. An attacker who supplies inconsistent shares at indexes `> t` creates keys whose fixed `group_key()` value no subset containing those indexes can ever produce a valid aggregate signature for — while `ThresholdView::group_key` still reports the 1..=t-derived key (`crypto/dkg/src/lib.rs:523-532`).

This mirrors the reported bug class exactly: oracle-/peer-reported values (`verification_shares`, `secret_share`) are stored and used to derive a value (`group_key` ≙ `activeCount`/`preExitingBalance`) without enforcing that the reported values are consistent with each other or bounded by the underlying "real" quantities (the discrete logs of the shares).

### Impact Explanation
An unprivileged party that can feed bytes to `ThresholdKeys::read` (the listed reachable surface, e.g. key-recovery/migration flows that serialize `ThresholdKeys` between parties) can hand a node `ThresholdKeys` whose `group_key()` — used to derive the multisig's Bitcoin address and to verify aggregate FROST signatures — corresponds to a key inconsistent with the shares at indexes `> t`, or whose `secret_share` doesn't match `verification_shares[i]`. The node will report/control an address whose funds are unspendable by the honest signing set (no subset containing a poisoned index can satisfy the fixed `group_key`), or emit partial signatures that fail verification, permanently attributing fault to the victim. This is a "funds reported received but not spendable" / incorrect verifier-relation impact, matching Medium severity.

### Likelihood Explanation
Requires the attacker to supply the deserialized key bytes (reachable via `ThresholdKeys::read` on peer-supplied key material); not exploitable by purely on-chain message data. Once supplied, the inconsistency is silent — `read`/`new` succeed and the corrupted `group_key` propagates into address derivation (`processor/src/key_gen.rs:57-62` stores/loads these keys and derives key identities from `group_key().to_bytes()`).

### Recommendation
In `ThresholdKeys::new`, enforce:

- `C::generator() * secret_share == verification_shares[&params.i()]`
- consistency of all `n` verification shares with a single polynomial of degree `t-1`, e.g. verify that every subset/interpolation yields the same `group_key` (or verify each share against a published commitment to the shared polynomial rather than only the first `t` shares).

### Proof of Concept
Construct a byte stream for `ThresholdKeys::<C>::read` with `t = 2`, `n = 3`, honest Lagrange shares for indexes 1–2 defining `group_key K`, and an arbitrary `verification_shares[3]` not lying on the same polynomial. `read` succeeds; `keys.view(vec![1,2,3])` interpolates a secret/verification-share set inconsistent with `K`, so any signing session including participant 3 produces an aggregate Schnorr signature that fails verification under `keys.group_key() == K`, while `group_key()` still reports `K` as the multisig's identity. Additionally, setting `secret_share` to any scalar unrelated to `verification_shares[i]` is accepted without error. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Note on confidence: reachability depends on an integration feeding peer-controlled bytes to `ThresholdKeys::read` (e.g. a recovery/migration path); within `processor/src/key_gen.rs` the bytes come from a local DB, so real-world exposure depends on how `dkg` recovery transports keys — I could not fully verify a wire-level delivery path within the in-scope crates. The missing invariants themselves are confirmed at the cited lines.

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

**File:** crypto/dkg/src/lib.rs (L494-532)
```rust
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

    /*
      The offset is included by adding it to the participant with the lowest ID.

      This is done after interpolating to ensure, regardless of the method of interpolation, that
      the method of interpolation does not scale the offset. For Lagrange interpolation, we could
      add the offset to every key share before interpolating, yet for Constant interpolation, we
      _have_ to add it as we do here (which also works even when we intend to perform Lagrange
      interpolation).
    */
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;

    Ok(ThresholdView {
      interpolation: self.core.interpolation.clone(),
      scalar: self.scalar,
      offset: self.offset,
      group_key: self.group_key(),
      secret_share,
      original_verification_shares: self.core.verification_shares.clone(),
      verification_shares,
      included,
    })
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

**File:** processor/src/key_gen.rs (L45-63)
```rust
impl GeneratedKeysDb {
  #[allow(clippy::type_complexity)]
  fn read_keys<N: Network>(
    getter: &impl Get,
    key: &[u8],
  ) -> Option<(Vec<u8>, (Vec<ThresholdKeys<Ristretto>>, Vec<ThresholdKeys<N::Curve>>))> {
    let keys_vec = getter.get(key)?;
    let mut keys_ref: &[u8] = keys_vec.as_ref();

    let mut substrate_keys = vec![];
    let mut network_keys = vec![];
    while !keys_ref.is_empty() {
      substrate_keys.push(ThresholdKeys::read(&mut keys_ref).unwrap());
      let mut these_network_keys = ThresholdKeys::read(&mut keys_ref).unwrap();
      N::tweak_keys(&mut these_network_keys);
      network_keys.push(these_network_keys);
    }
    Some((keys_vec, (substrate_keys, network_keys)))
  }
```
