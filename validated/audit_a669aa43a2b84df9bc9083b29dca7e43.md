### Title
`ThresholdKeys::read` accepts semantically inconsistent key material — secret share is never verified against the deserialized verification shares, allowing silent substitution of the group key - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` deserializes `t`, `n`, `i`, an interpolation method, a `secret_share`, and `n` verification shares, then passes them to `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` checks the *shape* of the input (share count, participant bounds, interpolation applicability) and derives `group_key` from `verification_shares[1..=t]`, but never checks the essential semantic invariant: `C::generator() * secret_share == verification_shares[i]` [2](#0-1) . The codebase itself acknowledges this gap: `AlgorithmSignatureMachine::complete` notes the only way to reach its "everyone had a valid share yet the signature was still invalid" branch is "to deserialize a semantically invalid FrostKeys" [3](#0-2) .

### Finding Description
This is the direct analog of the PraisonAI bug class: a parser that enforces only syntactic/structural checks while skipping the validation that makes the input safe to act on. `read`/`new` validate:

- participant indexes are nonzero and ≤ n (`ThresholdParams::new`, `Participant::new`) [4](#0-3) 
- exactly `n` verification shares are present [5](#0-4) 
- `Constant` interpolation only when `t == n` [6](#0-5) 

but nothing binds the deserialized `secret_share` to `verification_shares[i]`, and nothing binds the `Constant` coefficient vector to the shares (with `Interpolation::Constant`, `interpolation_factor(i)` is just `c[i-1]`, and `group_key` is computed as `Σ verification_shares[i] * c[i-1]` over `1..=t` [7](#0-6) ). An attacker who supplies the serialized bytes therefore fully controls the resulting `group_key`, the secret share used by `ThresholdView::secret_share()` during signing [8](#0-7) , and the per-participant verification shares used for blame in `complete` [9](#0-8) .

### Impact Explanation
A crafted `ThresholdKeys` blob is accepted without error and produces a `group_key()` and `view()` the holder then uses in `SignMachine`/`AlgorithmSignatureMachine` flows. Two concrete consequences:

1. **Signing under an attacker-chosen key**: by supplying `verification_shares` (and `Constant` coefficients) derived from a polynomial the attacker controls, `group_key` becomes a key whose discrete log the attacker knows, while the node believes it is operating its real multisig keys. Any signing operation performed with these keys produces shares for a group key unrelated to the intended validator set — funds or protocol messages keyed by `group_key()` are misattributed.
2. **Unattributable aborts**: when the crafted `secret_share` is inconsistent with the verification shares, every honest share fails batch verification yet the aggregate can't be validated, terminating in `FrostError::InternalError` [10](#0-9)  — a persistent signing halt that blame cannot assign.

The second case alone is a hard DoS of all signing for that key set; the first is a signing-target substitution if `ThresholdKeys` bytes are ever loaded from data an unprivileged party can influence (the serialization is a plain length-prefixed format with no MAC or consistency proof).

### Likelihood Explanation
Reachability is the limiting factor: in Serai's processor, `ThresholdKeys::read` is applied to locally persisted keys (`GeneratedKeysDb::read_keys` unwraps it over DB contents [11](#0-10) ), so exploitation requires influencing those bytes rather than simply sending a protocol message. Within the threat model allowed for this scan (untrusted bytes fed to `ThresholdKeys::read`), the missing check is unconditional — there is no code path that would catch an inconsistent `(secret_share, verification_shares, interpolation)` tuple before it is used for signing. Medium-to-high confidence in the flaw; likelihood depends on the persistence/channel integrity of the serialized keys.

### Recommendation
In `ThresholdKeys::new` (covering both `read` and programmatic construction), verify `C::generator() * secret_share == verification_shares[&params.i()]` and reject otherwise. Additionally consider committing the serialized form (e.g., keying the DB by `group_key` and re-checking on load, or authenticating stored keys), since the format carries no integrity protection and `read` is the only gate before signing.

### Proof of Concept
```rust
// Construct a "valid" ThresholdKeys whose secret share does not match its
// claimed verification share / group key.
let mut buf = vec![];
buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
buf.extend(C::ID);
buf.extend(2u16.to_le_bytes()); // t = 2
buf.extend(3u16.to_le_bytes()); // n = 3
buf.extend(1u16.to_le_bytes()); // i = 1
buf.push(1);                    // Interpolation::Lagrange
// Attacker-chosen secret share
buf.extend(attacker_scalar.to_repr().as_ref());
// Attacker-chosen verification shares (e.g., from their own polynomial,
// or honest-looking points unrelated to attacker_scalar)
for vs in attacker_verification_shares {
    buf.extend(vs.to_bytes().as_ref());
}
let keys = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap(); // accepted
// keys.group_key() is derived from attacker_verification_shares[1..=2],
// yet keys.params().i() == 1 signs with attacker_scalar which does NOT
// correspond to verification_shares[1] — no error is raised.
```

### Citations

**File:** crypto/dkg/src/lib.rs (L166-179)
```rust
  pub const fn new(t: u16, n: u16, i: Participant) -> Result<ThresholdParams, DkgError> {
    if (t == 0) || (n == 0) {
      return Err(DkgError::ZeroParameter { t, n });
    }

    if t > n {
      return Err(DkgError::InvalidThreshold { t, n });
    }
    if i.0 > n {
      return Err(DkgError::InvalidParticipant { n, participant: i });
    }

    Ok(ThresholdParams { t, n, i })
  }
```

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

**File:** crypto/dkg/src/lib.rs (L494-521)
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

**File:** crypto/frost/src/sign.rs (L474-494)
```rust
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
```

**File:** processor/src/key_gen.rs (L47-62)
```rust
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
```
