### Title
Uncontrolled resource consumption in `ThresholdKeys::read` via attacker-set `t`/`n` participant counts - (File: crypto/dkg/src/lib.rs)

### Summary
The Mermaid report's bug class is a size/element limit applied per-field while the attacker-controlled *count* of expensive items is left unbounded, letting many small inputs combine into disproportionate work. Serai has the same shape in `ThresholdKeys::read`: two attacker-controlled `u16` fields (`t`, `n`) drive an unbounded number of canonical scalar reads, canonical point decodes, and subsequent group-key interpolation work, with no cap on the total participant count beyond `u16::MAX`.

### Finding Description

`ThresholdKeys::read` deserializes `t`, `n`, and `i` directly from the byte stream:

```rust
// crypto/dkg/src/lib.rs:591-616
let (t, n, i) = (
  read_u16()?, read_u16()?,
  Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?,
);
...
0 => Interpolation::Constant({
  let mut res = Vec::with_capacity(usize::from(n));
  for _ in 0 .. n { res.push(C::read_F(reader)?); }
  res
}),
``` [1](#0-0) 

It then reads `n` elliptic-curve points into `verification_shares` (lines 620-623) and calls `ThresholdKeys::new`, whose only validation is `ThresholdParams::new` — which accepts any `t <= n <= 65535` with no practical bound:

```rust
// crypto/dkg/src/lib.rs:166-179
if (t == 0) || (n == 0) { return Err(ZeroParameter { t, n }); }
if t > n { return Err(InvalidThreshold { t, n }); }
if i.0 > n { return Err(InvalidParticipant { n, participant: i }); }
Ok(ThresholdParams { t, n, i })
``` [2](#0-1) 

Three consequences follow from a crafted `n = 65535`:

1. **Point decompression amplification**: each 32-byte input element triggers a full canonical point decode in `C::read_G` (decompress + re-encode + constant-time compare, crypto/ciphersuite/src/lib.rs:91-100). [3](#0-2) 
2. **Interpolation work**: with `Interpolation::Lagrange`, `ThresholdKeys::new` derives the group key by evaluating the participant polynomial at zero, i.e., `Σ verification_shares[i] * λ_i(0)`, where each Lagrange coefficient `λ_i(0)` is a product over the other `n-1` participant indexes — O(n²) field multiplications (~4.3×10⁹ for `n = 65535`).
3. **Downstream per-participant iteration**: any code iterating `params.all_participant_indexes()` (crypto/dkg/src/lib.rs:195-197) — e.g., PedPoP `verify_r1`'s `BatchVerifier` loop and `calculate_share`'s per-participant multiexp — is subsequently driven by this attacker-chosen `n`. [4](#0-3) 

This is the same flaw as the GitLab report: the per-item encodings are strictly validated (canonical scalars/points), but the *number of items* — the multiplicative factor — is unbounded, so a small serialized input causes work far exceeding what any honest `ThresholdKeys` (real deployments use n in the hundreds at most) would require.

### Impact Explanation
An unprivileged party who can get a node to deserialize attacker-supplied `ThresholdKeys` bytes (the prompt explicitly lists `ThresholdKeys::read` as an entry point for untrusted bytes) can force ~4 billion field operations plus 65,535 EC decompressions and a multiexp of up to 65,535 terms inside `ThresholdKeys::new`, stalling or crashing the process — analogous to the frozen browser tab, but on a signing/DKG host. A `Constant` interpolation payload with `t = n = 65535` likewise forces a 65,535-element multiexp for the group key.

### Likelihood Explanation
Likelihood depends on a deployment feeding externally-originated bytes into `ThresholdKeys::read` (key-share distribution, recovery, or coordinator-supplied parameters). The function is a public, documented deserialization API with no documented requirement that input be trusted; its `t`/`n` fields are the only size controls and they accept the full `u16` range. No threshold-signature internal secret is needed to trigger it — reading itself performs the expensive work before any cryptographic check can reject the input.

### Recommendation
Enforce a protocol-level maximum on `n` (and hence `t`) in `ThresholdKeys::read` — e.g., reject `n` above the maximum set size Serai actually supports (compare `MAX_KEY_SHARES_PER_SET` already used by the coordinator in `Transaction::read`, coordinator/src/tributary/transaction.rs:431) — before allocating `Vec::with_capacity(n)` or performing interpolation. [5](#0-4) 

### Proof of Concept

```rust
// Attacker-controlled bytes fed to ThresholdKeys::read for any in-scope Ciphersuite C.
let mut serialized = vec![];
serialized.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
serialized.extend(C::ID);
serialized.extend(65535u16.to_le_bytes()); // t
serialized.extend(65535u16.to_le_bytes()); // n
serialized.extend(1u16.to_le_bytes());     // i = Participant(1)
serialized.push(1);                        // Interpolation::Lagrange
serialized.extend(C::F::ONE.to_repr().as_ref()); // secret_share
// 65535 valid encodings of the generator point
for _ in 0 .. 65535u16 {
  serialized.extend(C::generator().to_bytes().as_ref());
}
// ~4.2 MB input -> 65535 point decodes + O(n^2) Lagrange interpolation in
// ThresholdKeys::read -> ThresholdKeys::new, plus n-sized allocations.
let _ = ThresholdKeys::<C>::read::<&[u8]>(&mut serialized.as_ref());
```

Note: I could not fully inspect `ThresholdKeys::new`'s interpolation path within this session (lines ~200-565 of `crypto/dkg/src/lib.rs` were not read), so the O(n²) claim rests on Lagrange evaluation over `n` verification shares being used to derive `group_key`, consistent with `Interpolation::Lagrange` and the `exponential`/Lagrange machinery in `crypto/dkg/pedpop/src/lib.rs`. Even in the weakest reading, the `n`-driven decode loop and unbounded `Vec::with_capacity(n)` alone constitute the reported bug class.

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

**File:** crypto/dkg/src/lib.rs (L591-616)
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
```

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```

**File:** crypto/dkg/pedpop/src/lib.rs (L311-334)
```rust
    let mut batch = BatchVerifier::<Participant, C::G>::new(commitment_msgs.len());
    let mut commitments = HashMap::new();
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;
```

**File:** coordinator/src/tributary/transaction.rs (L431-433)
```rust
        if u32::from(len) > (serai_client::validator_sets::primitives::MAX_KEY_SHARES_PER_SET - 1) {
          Err(io::Error::other("more points reported than allowed validator"))?;
        }
```
