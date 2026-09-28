### Title
Attacker-controlled `n` in `ThresholdKeys::read` pre-allocates a 65535-element scalar vector before any threshold-parameter validation - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` trusts the serialized `n` field (a `u16` up to 65,535) and immediately calls `Vec::with_capacity(usize::from(n))` when the interpolation byte selects `Interpolation::Constant`, allocating heap capacity for up to 65,535 scalars (~2 MiB for a 32-byte field) before `ThresholdParams::new` / `ThresholdKeys::new` validate that `n` is consistent with `t`, `i`, or the actual bytes present. This is the same bug class as the Zebra advisory: a deserializer allocates against a loose transport-derived ceiling instead of the tighter protocol limit, letting a tiny input force a large preallocation.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` parses, in order: a curve-ID length and ID, `t`, `n`, `i` as raw `u16`s, then an interpolation variant byte. For variant `0` it executes: [1](#0-0) 

The `n` used for `Vec::with_capacity` and the subsequent `read_F` loop is the raw attacker-supplied `u16`; only after reading `n` scalars, one `secret_share`, and `n` verification-share points does `ThresholdParams::new(t, n, i)` reject invalid combinations: [2](#0-1) 

Two amplification properties follow:

1. **Preallocation without data**: `Vec::with_capacity(usize::from(n))` reserves `n × size_of::<C::F>()` bytes of heap the moment the interpolation byte is read. An attacker only needs to send the ID length + ID + 6 bytes of `t`/`n`/`i` + 1 interpolation byte (~20 bytes total) to trigger a ~2 MiB allocation; the first `read_F` then fails on EOF, but the allocation (and its zeroing cost for `Zeroizing` wrappers in callers) already happened.

2. **Parse cost before validation**: when data *is* provided, the deserializer performs `n` scalar reads plus `n` point decodings (`read_G` per verification share, which includes point decompression/validation) before `ThresholdKeys::new` enforces that `t <= n`, `i <= n`, and share consistency — the tighter "protocol" limit analogous to Zebra's consensus checks being applied post-allocation.

`ThresholdKeys::read` is an explicitly in-scope sink for untrusted bytes (used for key material exchanged in multisig setup flows), and `C::read_G` performs full group decoding per element, so parse amplification is real work, not just memory.

The analogous pattern in `Interpolation::Constant` is the only preallocation site in the read path; other `with_capacity` uses in scope (`crypto/frost/src/sign.rs` `included`, `crypto/frost/src/nonce.rs` `nonces`, `crypto/dkg/musig/src/lib.rs`) size allocations from already-validated structures (`preprocesses.len()`, `planned_nonces`, `keys.len()` checked via `check_keys`), not raw wire fields. `coordinator/src/tributary/transaction.rs` `DkgShares` (which multiplies attacker `share_quantity × key_share_quantity × share_len` with no `TRANSACTION_SIZE_LIMIT` check, unlike `DkgCommitments`) and `coordinator/tributary/src/block.rs` `Vec::with_capacity(u32 txs)` show the same class but are outside the declared in-scope crates.

### Impact Explanation
Unauthenticated memory/CPU amplification on any code path that calls `ThresholdKeys::read` on peer- or coordinator-supplied bytes: ~20 bytes of input forces a multi-MiB heap reservation, and a padded input forces 65,535 point decompressions before rejection. Stackable per-message/connection, matching the Zebra "bounded but amplified" Medium-severity DoS profile.

### Likelihood Explanation
Reachable wherever threshold-key material is deserialized from serialized blobs during multisig/DKG setup — the report's own scope lists `ThresholdKeys::read` as an untrusted-bytes sink. No authentication, valid proof, or correct threshold parameters are needed to reach the `with_capacity` line; only the matching `C::ID` prefix is required, which is public. The write side (`ThresholdKeys::write`) never emits `n > u16::MAX`, but nothing caps `n` to the honest participant bound before allocation.

### Recommendation
Validate `t`, `n`, `i` via `ThresholdParams::new` (and a sane upper bound on `n`, e.g. the protocol's maximum participant count) *before* the interpolation match; replace `Vec::with_capacity(n)` and the `1 ..= n` `read_G` loop so allocation/read counts are only performed after parameter validation, or read incrementally without pre-reserving `n` elements.

### Proof of Concept
```rust
use std::io;
use ciphersuite::Ciphersuite;
use dkg::ThresholdKeys;
// For any in-scope C (e.g. ciphersuite Ristretto):
let mut bytes = Vec::new();
bytes.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes()); // id_len
bytes.extend(C::ID);                                            // matching curve ID
bytes.extend(1u16.to_le_bytes());                               // t = 1
bytes.extend(u16::MAX.to_le_bytes());                           // n = 65535
bytes.extend(1u16.to_le_bytes());                               // i = 1
bytes.push(0);                                                  // Interpolation::Constant
// No scalar bytes follow at all.
// ThresholdKeys::read still executes Vec::with_capacity(65535) for C::F
// (~2 MiB heap reservation) before the first read_F hits EOF.
let res: io::Result<ThresholdKeys<C>> = ThresholdKeys::read(&mut bytes.as_slice());
assert!(res.is_err()); // errors only AFTER the amplified allocation
```

### Citations

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
