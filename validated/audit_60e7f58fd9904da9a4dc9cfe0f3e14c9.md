### Title
Crafted `ThresholdKeys` deserialization injects attacker-defined key material — secret share is never checked against the verification shares, enabling a fictitious multisig under attacker-known keys - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` reconstructs a threshold key set entirely from untrusted bytes — `t`, `n`, `i`, the interpolation method/coefficients, `secret_share`, and `verification_shares` — and `ThresholdKeys::new` accepts them without any self-consistency check. In particular, nothing verifies that `verification_shares[i] == G * secret_share` for the local participant, or that the shares correspond to any jointly-generated polynomial. An attacker who supplies the serialized bytes can therefore "inject" a wholly fabricated key set, analogous to how the Exponent CMS bug let the `fileid` parameter be injected into a privileged SQL context.

### Finding Description
`ThresholdKeys::read` parses every semantic field of the key set from the reader and passes them directly to `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` only checks the *quantity* of verification shares, that participant indexes are `<= n`, and that `Constant` interpolation implies `t == n` [2](#0-1) . It then computes `group_key` by interpolating `verification_shares[1..=t]` — treating all attacker-supplied values as authoritative [3](#0-2) .

No check binds `secret_share` to `verification_shares[params.i()]`. In the legitimate DKG flows this invariant is enforced cryptographically (PedPoP verifies each share against the sender's committed polynomial via `share_verification_statements` [4](#0-3) ), but the deserialization path bypasses all of it. The signing code itself acknowledges this gap: `complete()` notes that "the only known way to cause this... is to deserialize a semantically invalid FrostKeys" [5](#0-4) .

Two attacker-injectable shapes exist:

1. **Fully consistent fabricated keys**: attacker chooses `secret_share` and sets `verification_shares[i] = G * secret_share` (plus chosen shares/coefficients for other indexes). Deserialization succeeds, `view()` and FROST signing proceed normally, and signatures verify under a `group_key` whose discrete log decomposition is entirely attacker-known.
2. **Inconsistent share**: `secret_share` does not match `verification_shares[i]`; the victim emits a share that fails `verify_share` for everyone else — silently corrupting their view of which key they hold while `ThresholdKeys::read` reported success.

### Impact Explanation
An attacker able to feed serialized key bytes to `ThresholdKeys::read` (a coordinator/dealer distributing key files, a backup/restore path, or any stored material an unprivileged party can influence — the exact "untrusted bytes fed to `ThresholdKeys::read`" surface) can place the victim into a fictitious multisig whose group key the attacker controls. The attacker can then produce valid FROST signatures for that group key unilaterally, and any signature the victim honestly produces is a valid signature under attacker-known key material. In the inconsistent-share variant, the victim's signing participation is silently broken or framed as faulty (`FrostError::InvalidShare`), enabling targeted exclusion.

### Likelihood Explanation
Exploitation requires control over the bytes passed to `ThresholdKeys::read`, not just a wire message — comparable to the SQLi requiring influence over the `fileid` parameter. In architectures where keys are provisioned, migrated, or restored from files a coordinator can write, this is a reachable, single-shot injection with no protocol interaction required.

### Recommendation
In `ThresholdKeys::new` (or `read`), enforce `debug_assert`-free verification that `C::generator() * secret_share == verification_shares[&params.i()]`, rejecting semantically invalid serializations at load time rather than deferring failure to signing. Additionally, for `Interpolation::Constant`, consider validating that the recomputed `group_key` is consistent with an externally-pinned expected key fingerprint where one is available.

### Proof of Concept
```rust
// Attacker-forged serialization for curve C (conceptual):
// Choose arbitrary known scalar `a`, then encode:
//   t = n = 2 (Constant interpolation), i = victim index,
//   interpolation = 0 || c_0 || c_1           (attacker-chosen coeffs)
//   secret_share = a                          (attacker-KNOWN share)
//   verification_shares[1] = G*a, [2] = G*b   (consistent, known)
let keys = ThresholdKeys::<C>::read(&mut forged.as_ref()).unwrap();
// Succeeds. keys.group_key() interpolates attacker-known shares ->
// attacker knows dlog(group_key) and can forge FROST signatures alone.
```
The root cause is visible in `ThresholdKeys::new` accepting `secret_share` and `verification_shares` independently [6](#0-5)  and in `read` passing parsed bytes straight through [7](#0-6) .

Caveat: I was unable to fully trace all downstream consumers of `ThresholdKeys::read` (e.g., whether production callers pin an expected `group_key` after loading, which would reduce this to a consistency/DoS issue rather than key injection); the missing self-consistency check in `new`/`read` itself is confirmed in the code.

### Citations

**File:** crypto/dkg/src/lib.rs (L349-390)
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
```

**File:** crypto/dkg/src/lib.rs (L618-631)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L487-491)
```rust
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
```

**File:** crypto/frost/src/sign.rs (L491-494)
```rust
    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
```
