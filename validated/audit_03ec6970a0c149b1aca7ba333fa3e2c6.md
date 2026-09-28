### Title
Attacker-controlled serialized `ThresholdKeys` fields are accepted without consistency checks, letting crafted key material define a group key disconnected from the honest secret share and corrupt blame - ([File: crypto/dkg/src/lib.rs](https://github.com/serai/serai/blob/develop/crypto/dkg/src/lib.rs))

### Summary
Analogous to the Mattermost mass-assignment flaw (accepting request parameters the endpoint should not honor), `ThresholdKeys::read`/`ThresholdKeys::new` accept `t`, `n`, `i`, interpolation coefficients, `secret_share`, and `verification_shares` purely from supplied bytes, then derive `group_key` solely from `verification_shares[1..=t]` — without ever checking that `secret_share` matches `verification_shares[i]`, that the verification shares interpolate consistently, or that a `Constant` interpolation's coefficients are the intended binding factors.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) deserializes `t`, `n`, `i`, an `Interpolation` variant (including a fully attacker-chosen `Constant(Vec<F>)` coefficient list), a `secret_share`, and `n` verification shares from a byte stream, then calls `ThresholdKeys::new`. `ThresholdKeys::new` (lines 349-391) performs only structural checks:

- `verification_shares.len() == n` and all keys `<= n` (lines 355-365)
- `Constant` requires `t == n` (lines 367-374)

It then computes `group_key = Σ_{j=1..=t} verification_shares[j] * interpolation_factor(j, 1..=t)` (lines 376-378). Three fields are never cross-validated:

1. `secret_share` is never checked against `verification_shares[i]` (`G * secret_share == verification_shares[i]` is asserted only via `debug_assert` in `musig`, crypto/dkg/musig/src/lib.rs:152, not in `new`).
2. `verification_shares[j]` for `j > t` never influence `group_key` yet are used by `view()` for share verification — so they can be arbitrary points, including identity or points unrelated to any polynomial.
3. `Constant` coefficients are taken verbatim; `interpolation_factor` returns `c[i-1]` for whatever `i` is requested (lines 226-249), so a crafted coefficient list re-weights the victim's real secret share arbitrarily.

Because `view()` (lines 463-533) trusts `core.verification_shares` and `core.interpolation` wholesale, every signer set derived from a tampered blob produces verification shares and a group key the attacker defined.

### Impact Explanation
An unprivileged party who can cause a node to ingest crafted `ThresholdKeys` bytes (import/restore path, or any flow feeding untrusted bytes to `ThresholdKeys::read`, which is explicitly in-scope) makes the victim operate on a `ThresholdKeys` whose `group_key` is attacker-defined while the `secret_share` is the victim's genuine share. Consequences in `crypto/frost/src/sign.rs`:

- `AlgorithmSignMachine::sign` succeeds locally (the victim signs with its real share), but `AlgorithmSignatureMachine::complete` (sign.rs:447-495) verifies the summed share against the attacker-chosen `group_key` — which never equals `G * (interpolated real secret)` — so signing fails permanently for that key. If the group key bound funds, they become unspendable (the `group_key` no longer corresponds to the shared secret).
- In the batch blame path, `verify_share` checks each share against `view.verification_share(l)` built from the corrupted shares map; honest participants' valid shares are flagged `InvalidShare` (sign.rs:475-489), feeding false `InvalidParticipant`/slash reports to the processor (`processor/src/signer.rs`, `batch_signer.rs`, `slash_report_signer.rs`). This is the direct analog of "registering a user as inactive": attacker-supplied parameters silently mark honest participants as faulty.

### Likelihood Explanation
Reachability requires the victim to deserialize attacker-influenced key material — plausible wherever `ThresholdKeys::read` is used on bytes not generated locally (backup restore, key handoff, processor DB populated from external input). All attacker inputs are canonical scalars/points so they pass `read_F`/`read_G`; no secret knowledge is needed since the attack relies purely on supplying inconsistent-but-well-formed fields. Impact severity is Medium: persistent signing denial / fund lockup for the affected key plus incorrect blame attribution, rather than key recovery or forgery.

### Recommendation
In `ThresholdKeys::new`, additionally verify:

- `C::generator() * *secret_share == verification_shares[&params.i()]` (share/verification consistency),
- for `Interpolation::Constant`, that the supplied coefficients reproduce the intended binding factors (or reject `Constant` when keys arrive from untrusted deserialization),
- that `verification_shares` values are non-identity and, ideally, that the map's contents interpolate to the same `group_key` for other size-`t` subsets (consistency check against the claimed polynomial).

At minimum, `ThresholdKeys::read` should reject inputs where the deserialized `secret_share` does not match its own verification share.

### Proof of Concept
```rust
// Conceptual PoC against crypto/dkg (Ristretto)
// Build a blob: correct params, real secret_share s_i for participant i,
// but attacker-chosen verification_shares[1..=t] = G*y_j for known y_j.
// ThresholdKeys::read accepts it; group_key = G * sum(y_j * lambda_j) != G * real_secret.
// Any sign()/complete() under these keys fails signature verification, and
// honest shares are reported InvalidShare -> false blame.
// Sketch:
let mut buf = vec![];
buf.extend((Ristretto::ID.len() as u32).to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(t.to_le_bytes()); buf.extend(n.to_le_bytes());
buf.extend(i.0.to_le_bytes());
buf.push(1); // Interpolation::Lagrange
buf.extend(real_share_i.to_repr().as_ref());       // honest share, taken as-is
for j in 1..=n {
    buf.extend((Ristretto::generator() * y_j).to_bytes().as_ref()); // attacker shares
}
let keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice()).unwrap();
assert_ne!(keys.group_key(), Ristretto::generator() * real_group_secret);
// keys.view(included) now yields verification_shares inconsistent with peers'
// shares; complete() fails and honest participants are blamed.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crypto/frost/src/sign.rs (L447-495)
```rust
  fn complete(
    self,
    mut shares: HashMap<Participant, SignatureShare<C>>,
  ) -> Result<A::Signature, FrostError> {
    let params = self.params.multisig_params();
    validate_map(&shares, self.view.included(), params.i())?;

    let mut responses = HashMap::new();
    responses.insert(params.i(), self.share);
    let mut sum = self.share;
    for (l, share) in shares.drain() {
      responses.insert(l, share.0);
      sum += share.0;
    }

    // Perform signature validation instead of individual share validation
    // For the success route, which should be much more frequent, this should be faster
    // It also acts as an integrity check of this library's signing function
    if let Some(sig) = self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum) {
      return Ok(sig);
    }

    // We could remove blame_entropy by taking in an RNG here
    // Considering we don't need any RNG for a valid signature, and we only use the RNG here for
    // performance reasons, it doesn't feel worthwhile to include as an argument to every
    // implementor of the trait
    let mut rng = ChaCha20Rng::from_seed(self.blame_entropy);
    let mut batch = BatchVerifier::new(self.view.included().len());
    for l in self.view.included() {
      if let Ok(statements) = self.params.algorithm.verify_share(
        self.view.verification_share(*l),
        &self.B.bound(*l),
        responses[l],
      ) {
        batch.queue(&mut rng, *l, statements);
      } else {
        Err(FrostError::InvalidShare(*l))?;
      }
    }

    if let Err(l) = batch.verify_vartime_with_vartime_blame() {
      Err(FrostError::InvalidShare(l))?;
    }

    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
  }
```
