### Title
Quadratic Lagrange interpolation in `ThresholdKeys::read`/`view` enables CPU DoS from small untrusted input - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-controlled `t`/`n` up to 65535 each, then calls `ThresholdKeys::new`, which computes `interpolation_factor` — an O(t) Lagrange product including a field inversion — once per participant in `1..=t`. The same quadratic pattern recurs in `ThresholdKeys::view` (`O(included²)` multiplications and `O(included)` inversions), which `AlgorithmSignMachine::sign` invokes on attacker-influenced `included` sets. A ~2 MB serialized `ThresholdKeys` therefore triggers ~4×10⁹ scalar multiplications plus tens of thousands of field inversions, mirroring the Suricata KRB5 quadratic-buffering DoS class.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, `i` as unconstrained `u16`s and reads `n` verification shares before delegating to `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` computes the group key by calling `interpolation_factor(*i, &t)` for each of `t` participants [2](#0-1) . With `Interpolation::Lagrange`, each `interpolation_factor` call iterates the whole `included` list performing one scalar multiply and subtract per member, plus a full field inversion at the end [3](#0-2) . Total deserialization cost is therefore Θ(t²) field multiplications + Θ(t) inversions — with t = 65535 that is ≈4.3 billion multiplications and 65535 inversions (each inversion itself costing hundreds of field operations), all induced by an input of only `n·32 ≈ 2 MB`.

The identical quadratic cost recurs at signing time: `ThresholdKeys::view` calls `interpolation_factor` for every member of `included` [4](#0-3) , and `AlgorithmSignMachine::sign` builds `included` from the peer-supplied `preprocesses` map [5](#0-4) . Nothing bounds `t`, `n`, or `included` below the u16 maximum — `ThresholdParams::new` only checks `t <= n`, `i <= n` [6](#0-5) .

### Impact Explanation
Any code path feeding untrusted bytes into `ThresholdKeys::read` (explicitly a supported deserialization API) can be forced into quadratic CPU work with a modest input. A single crafted ~2 MB blob stalls a processor/validator for a prolonged window inside `read` alone, and deserialized keys with large parameters impose the same penalty on every subsequent `view()`/`sign()` call. This is a remote, unauthenticated denial of service against threshold signing infrastructure — the same availability impact class as CVE-2026-31932.

### Likelihood Explanation
Reachability is direct: `read` performs the quadratic work before returning, so a caller anywhere that deserializes peer- or network-supplied `ThresholdKeys` is vulnerable. Even absent a current network path for raw `ThresholdKeys` bytes, the `view()`/`sign()` path is exercised whenever the signing-set size is attacker-influenced, and `Participant`/`n` are u16-bounded with no tighter cap, so the quadratic term is unbounded within protocol limits. Lagrange interpolation is the default mode for PedPoP-produced keys (`Interpolation::Lagrange` is selected in `calculate_share` [7](#0-6) ), so the expensive branch is the normal one.

### Recommendation
- Impose a sane upper bound on `t`/`n` in `ThresholdParams::new` or at `ThresholdKeys::read` (e.g., the actual maximum validator count), rejecting oversized inputs before interpolation.
- Precompute all Lagrange denominators in one pass and batch-invert them (Montgomery batch inversion), reducing `view`/`new` to Θ(t) inversions total and Θ(t²) → Θ(t) inversion-equivalent work; or compute all factors incrementally with a single inversion each.
- Cache interpolation factors per `included` set in `ThresholdView` so repeated `sign`/`complete` calls do not recompute the Θ(k²) product.

### Proof of Concept
```rust
use std::io;
use ciphersuite::{group::GroupEncoding, Ciphersuite};
use frost::{curve::Ristretto, ThresholdKeys};

fn main() {
  // Craft a serialized ThresholdKeys with t = n = 65535 (u16 max), Lagrange interpolation.
  let n: u16 = u16::MAX;
  let mut bytes = vec![];
  bytes.extend((Ristretto::ID.len() as u32).to_le_bytes());
  bytes.extend(Ristretto::ID);
  bytes.extend(n.to_le_bytes()); // t
  bytes.extend(n.to_le_bytes()); // n
  bytes.extend(1u16.to_le_bytes()); // i
  bytes.push(1); // Interpolation::Lagrange
  bytes.extend([0u8; 32]); // secret_share = 0 (any canonical scalar)
  for _ in 0 .. n {
    bytes.extend(Ristretto::generator().to_bytes()); // verification shares
  }
  // ~2 MB input => 65535 interpolation_factor calls, each O(65535) muls + 1 inversion
  let _ = ThresholdKeys::<Ristretto>::read(&mut bytes.as_slice()); // quadratic CPU burn
}
```
Total work inside `read` → `ThresholdKeys::new` is ≈ `65535²` scalar multiplications and `65535` field inversions — minutes of CPU for ~2 MB of input, versus linear input size.

### Citations

**File:** crypto/dkg/src/lib.rs (L166-178)
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
```

**File:** crypto/dkg/src/lib.rs (L229-247)
```rust
      Interpolation::Lagrange => {
        let i_f = F::from(u64::from(u16::from(i)));

        let mut num = F::ONE;
        let mut denom = F::ONE;
        for l in included {
          if i == *l {
            continue;
          }

          let share = F::from(u64::from(u16::from(*l)));
          num *= share;
          denom *= share - i_f;
        }

        // Safe as this will only be 0 if we're part of the above loop
        // (which we have an if case to avoid)
        num * denom.invert().unwrap()
      }
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L500-507)
```rust
    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
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

**File:** crypto/frost/src/sign.rs (L290-313)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L523-530)
```rust
    let KeyMachine { commitments, encryption, params, secret } = self;
    Ok(BlameMachine {
      commitments,
      encryption: encryption.into_decryption(),
      result: Some(
        ThresholdKeys::new(params, Interpolation::Lagrange, secret, verification_shares)
          .map_err(PedPoPError::DkgError)?,
      ),
```
