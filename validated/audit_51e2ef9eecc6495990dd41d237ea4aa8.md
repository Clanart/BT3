Let me check a couple of remaining details on `Participant` deserialization and `validate_map`.### Title
`ThresholdKeys::read` trusts attacker-supplied participant index, secret share, and verification shares without checking `G * secret_share == verification_shares[i]`, letting an attacker craft keys that make an honest signer emit provably invalid shares - (`crypto/dkg/src/lib.rs`)

### Summary
The analog of CVE-2018-11783 — where an Apache Traffic Server plugin failed to strip/override attacker-controlled headers, so client-injected fields were trusted as internally-set values — is the trust placed in attacker-serialized fields inside `ThresholdKeys::read` / `ThresholdKeys::new`. The participant index `i`, the `secret_share`, and the `verification_shares` map are all read verbatim from untrusted bytes, and the constructor never re-derives or cross-checks the one field that must be internally consistent: that the holder's own verification share equals `generator * secret_share`. The group key itself is *recomputed* from the first `t` verification shares, so two independently-controlled values (share vs. verification shares) flow into the same trusted object unvalidated.

### Finding Description
`ThresholdKeys::read` deserializes `(t, n, i)`, the interpolation, `secret_share`, and `n` verification shares directly from the reader [1](#0-0) . `ThresholdKeys::new` validates only structural properties: `verification_shares.len() == n`, each participant index `<= n`, and Constant interpolation requiring `t == n` [2](#0-1) . It computes `group_key` as the interpolation-weighted sum of `verification_shares[1..=t]` — never touching `secret_share` — and stores the attacker-supplied `i` and `secret_share` as-is.

There is no check that `C::generator() * secret_share == verification_shares[&params.i()]`, nor that the verification shares are mutually consistent with any actual polynomial. This is the "unstripped header" pattern: fields that should be derived or verified are instead taken on faith from the wire.

Downstream, `ThresholdKeys::view()` interpolates the (unvalidated) `secret_share` into `secret_share` of the `ThresholdView`, while interpolating the (unvalidated) `verification_shares` separately [3](#0-2) . `AlgorithmSignMachine::sign` then signs with `view.secret_share` and every other participant verifies that share against `view.verification_shares[i]` via `verify_share` [4](#0-3) .

Note that `recover_key` in the recovery crate *does* check `C::generator() * res == group_key` [5](#0-4) , proving the codebase authors know this invariant must hold — but `ThresholdKeys::new`/`read`, the entry point that constructs the object used for signing, omits it.

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ThresholdKeys::read` (an explicitly in-scope reachable API) produces a `ThresholdKeys` where the victim's own share cannot satisfy the victim's own verification share. When the victim participates in FROST signing:

1. `view()` interpolates the mismatched `secret_share` honestly.
2. `sign()` emits share `s_i = λ_i·secret_share + nonce·binding`.
3. Other participants run `verify_share` against `verification_shares[i]` — which encodes a *different* discrete log.
4. The victim's share fails verification; `complete` returns `FrostError::InvalidShare(victim_i)`.

In Serai's deployment, `InvalidShare`/`InvalidParticipant` reports propagate to the coordinator, which issues `fatal_slash` against the identified party. The victim — whose only "fault" was holding attacker-crafted key bytes — is blamed as a malicious signer and fatally slashed, causing direct economic harm (bonded stake loss). Alternatively, an attacker can set `i` to an arbitrary in-range participant index, combining this with the offset-adds-to-`included[0]` logic in `view()` [6](#0-5)  to create views whose scalar/offset application no honest party can reproduce.

Severity: Medium — attacker-controlled deserialization enables remote framing of an honest threshold signer for slashing, but requires an ingestion path feeding untrusted bytes into `ThresholdKeys::read`.

### Likelihood Explanation
The reachability requirement — untrusted bytes to `ThresholdKeys::read` — is a documented usage surface in this codebase's threat model. The bug requires no brute force, no cryptographic break, and no collusion: a single malformed blob deterministically produces an inconsistent key set that passes all existing validation (`new` only checks counts and bounds). However, it does presuppose an integrator path that deserializes key material from an unauthenticated source rather than from the DKG machine outputs, which reduces practical likelihood.

### Recommendation
In `ThresholdKeys::new` (crypto/dkg/src/lib.rs), add an explicit consistency check before constructing the key set:

- Verify `C::generator() * secret_share.deref() == verification_shares[&params.i()]` (rejecting if `params.i()` is absent from the map).
- Optionally verify the `group_key` derivation is consistent with `interpolation.interpolation_factor` applied to the first `t` shares (already computed — just assert non-identity and consistency).

This converts implicit trust in serialized fields into re-derived verification, matching the fix pattern of CVE-2018-11783 (strip/override rather than trust inbound fields).

### Proof of Concept
Conceptual (the cryptographic primitive operations are available in-scope):

```rust
// Attacker crafts ThresholdKeys bytes where secret_share does NOT
// correspond to verification_shares[i].
let t = 2u16; let n = 3u16; let i = 1u16;

// Pick arbitrary verification shares (e.g., a real key set's shares,
// or attacker-generated G*x_j values).
let verification_shares = attacker_chosen_shares; // map {1,2,3 -> G*x_j}

// Supply a secret_share s' != x_1 (e.g., random scalar).
let secret_share = random_scalar;

// ThresholdKeys::new succeeds: it only checks len, bounds, interpolation kind.
let keys = ThresholdKeys::new(
    ThresholdParams::new(t, n, Participant::new(i).unwrap()).unwrap(),
    Interpolation::Lagrange,
    Zeroizing::new(secret_share),
    verification_shares,
).unwrap(); // <-- accepted despite G*s' != verification_shares[1]

// group_key() = interpolated verification_shares -- encodes dlog != any
// combination that includes s' at index 1.
// keys.view([1,2]) produces secret_share = λ_1*s', verification_shares
// encoding λ_j*x_j. Signing with this view emits a share that fails
// verify_share at every other participant -> InvalidShare(1) -> victim blamed.
```

The vulnerable acceptance happens at `crypto/dkg/src/lib.rs:349-391` (`ThresholdKeys::new`) reachable via `ThresholdKeys::read` at `crypto/dkg/src/lib.rs:574-632`.

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

**File:** crypto/dkg/src/lib.rs (L493-533)
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

**File:** crypto/frost/src/sign.rs (L283-313)
```rust
  fn sign(
    mut self,
    mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, SignatureShare<C>), FrostError> {
    let multisig_params = self.params.multisig_params();

    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;
```

**File:** crypto/dkg/recovery/src/lib.rs (L80-82)
```rust
  if (C::generator() * res.deref()) != first_keys.group_key() {
    Err(RecoveryError::Failure)?;
  }
```
