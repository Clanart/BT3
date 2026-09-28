### Title
`ThresholdKeys::read` performs attacker-controlled allocations and deserialization before validating `n` — uncontrolled resource consumption - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` accepts a fully attacker-controlled byte stream and trusts the serialized `n` parameter to drive both a `Vec::with_capacity(n)` allocation and two `n`-iteration deserialization loops, all before `ThresholdParams::new` is invoked to check that `t`, `n`, and `i` are sane. An unauthenticated party that can feed bytes to `ThresholdKeys::read` (e.g., serialized threshold keys delivered during setup/recovery flows) can force an allocation of ~2 MiB and thousands of scalar/point deserializations from a handful of bytes, repeatedly, causing memory exhaustion / CPU burn — the same uncontrolled-resource-consumption class as CVE-2023-38210, where a malicious input drives excessive resource use.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` reads `t`, `n`, and `i` as raw `u16`s (lines 591–602). Immediately afterward, on the `Interpolation::Constant` branch it executes `Vec::with_capacity(usize::from(n))` and loops `C::read_F(reader)` `n` times (lines 607–613). Independently of the interpolation variant, it then loops `(1 ..= n)` calling `<C as Ciphersuite>::read_G(reader)` to populate `verification_shares` (lines 620–623). Only after all of this attacker-sized work is complete does it call `ThresholdParams::new(t, n, i)` inside `ThresholdKeys::new`, which is the first point at which `n` is semantically validated (lines 625–631).

The byte stream controls `n` (up to 65535). A 6-byte crafted header therefore triggers:

- an upfront heap allocation of `65535 * size_of::<C::F>()` bytes (`Vec::with_capacity` commits the memory even though no corresponding input data exists — unlike chunked readers, there is no early exit on EOF before the allocation), and
- up to 65535 canonical scalar decodes plus up to 65535 point decodes (each `read_G` performs full canonical/torsion checking, e.g., curve25519 point decompression) when a backing buffer of junk is supplied.

The ordering defect is that validation (`ThresholdParams::new`) runs strictly after the resource-consuming loops, instead of bounding `n` against `MAX_KEY_SHARES_PER_SET`-style limits (as the coordinator does for `DkgCommitments`, which explicitly rejects `commitments_len * each_commitments_len > TRANSACTION_SIZE_LIMIT` before allocating, and `SlashReport`, which rejects `len > MAX_KEY_SHARES_PER_SET - 1` before allocating). The in-scope crypto crate lacks any equivalent pre-read bound.

### Impact Explanation
Denial of service in the context of the current user, matching the CVE's impact profile (CVSS A:H). A single short input causes a ~2 MiB committed allocation plus tens of thousands of decompression operations. Because `read` takes any `io::Read`, an attacker can stream data slowly to keep the deserializer burning CPU in the `read_G`/`read_F` loops, and can repeat the call unboundedly — each invocation commits fresh memory proportional to the forged `n` before erroring. In a processor/coordinator context deserializing `ThresholdKeys` from remote or stored untrusted sources, this enables memory-pressure and CPU-exhaustion DoS of the signing/DKG component with only a few bytes of input.

### Likelihood Explanation
Reachability requires an integrator to deserialize `ThresholdKeys` from attacker-influenced bytes (e.g., a recovery/reshare blob, or key material provided by another participant). `ThresholdKeys::read` is a public API explicitly intended for `io::Read` sources; the crate performs no length/domain framing of its own. Exploitation needs no privileges and no valid cryptographic material — just a crafted `n` field — mirroring the CVE's "victim opens a malicious file" trigger. Impact is capped per call by `u16` (~4–8 MiB total work), but unlimited repetition makes it a practical DoS; consistent with Medium.

### Recommendation
Validate `n` before allocating or looping:

1. Call `ThresholdParams::new(t, n, i)` (or at minimum bound `n` against a protocol maximum such as the participant-count cap) immediately after reading `t`, `n`, `i`, before the interpolation branch and before the verification-share loop.
2. Replace `Vec::with_capacity(usize::from(n))` with incremental `push`es so a forged `n` cannot commit memory ahead of the data actually present — the same chunked-read defense `networks/ethereum/src/machine.rs::Call::read` uses for exactly this "claim a huge length for a few bytes" attack.
3. Consider capping `n` at the consensus-level `MAX_KEY_SHARES_PER_SET` rather than `u16::MAX`.

### Proof of Concept
```rust
use std::io::Cursor;
use frost::{curve::Secp256k1, dkg::ThresholdKeys};
use ciphersuite::Ciphersuite;

// Attacker crafts: valid C::ID, then t=1, n=65535, i=1, interpolation=Constant(0)
let mut bytes = Vec::new();
bytes.extend(u32::try_from(<Secp256k1 as Ciphersuite>::ID.len())
    .unwrap().to_le_bytes());
bytes.extend(<Secp256k1 as Ciphersuite>::ID);
bytes.extend(1u16.to_le_bytes());      // t
bytes.extend(65535u16.to_le_bytes());  // n  <- attacker controlled
bytes.extend(1u16.to_le_bytes());      // i
bytes.push(0u8);                       // Interpolation::Constant

// ThresholdKeys::read allocates Vec::with_capacity(65535) (~2 MiB) and attempts
// 65535 scalar reads before ThresholdParams::new ever runs. Repeating this call
// exhausts memory/CPU; supplying a large junk buffer additionally forces 65535
// read_F + 65535 read_G decodes.
let _ = ThresholdKeys::<Secp256k1>::read(&mut Cursor::new(&bytes));
```

Relevant code: `crypto/dkg/src/lib.rs` lines 591–631 — `n` read at 597–601, `Vec::with_capacity(usize::from(n))` at 608, the `read_G` loop over `1 ..= n` at 620–623, and validation deferred to `ThresholdKeys::new` at 625–631. Contrast with the length checks performed before allocation in `coordinator/src/tributary/transaction.rs` (`DkgCommitments` size-limit check at lines 286–290, `SlashReport` bound at lines 431–434), which this crate lacks.