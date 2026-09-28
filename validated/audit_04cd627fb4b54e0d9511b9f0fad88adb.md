### Title
Attacker-controlled `n` in `ThresholdKeys::read` triggers disproportionate heap allocation (memory DoS) - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes `n` (the number of participants) as a raw `u16` from untrusted bytes and immediately calls `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` coefficients, before validating `n` against `t`/`i` via `ThresholdParams::new`. A ~10-byte input therefore forces an allocation of up to `65535 * size_of::<C::F>()` (~2 MB per call, ~4 MB for larger field representations), and the allocation happens even though the subsequent `read_F` loop fails immediately on a short input. This is the same bug class as CVE-2021-29509 (CWE-400, uncontrolled resource consumption reachable by an unprivileged remote party), mapped onto Serai's untrusted deserialization path. [1](#0-0) 

### Finding Description
In `ThresholdKeys::read` (`crypto/dkg/src/lib.rs`):

1. `t`, `n`, `i` are each read as unvalidated `u16` values from the reader (lines 591-602).
2. If the interpolation tag byte is `0`, `Interpolation::Constant` is built with `Vec::with_capacity(usize::from(n))` (line 608) — `with_capacity` reserves the full `n * size_of::<C::F>()` immediately, before a single coefficient byte is required to be present.
3. The `n` coefficients are then read with `C::read_F` in a loop (lines 609-611), and `n` verification shares are read into a `HashMap` (lines 620-623).
4. Only at the very end is `ThresholdParams::new(t, n, i)` invoked to check `n`/`t` consistency (lines 625-631) — after all attacker-driven allocation and read work has already been spent.

The structural defect is that a 2-byte length field controls a multi-megabyte reservation and a multi-thousand-iteration deserialization loop, with no semantic bound check beforehand and no check that the input actually contains the claimed data before reserving memory for it. Compare `SignData::read`/`Transaction::read` in the coordinator, which check computed sizes against `TRANSACTION_SIZE_LIMIT` before allocating — `ThresholdKeys::read` has no equivalent guard, and unlike `Call::read` in `networks/ethereum/src/machine.rs` (which explicitly chunks reads to cap the DoS at 1 KB, with a comment acknowledging "A valid DoS would be to claim a 4 GB data is present for only 4 bytes"), no such mitigation exists here.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (the rules explicitly list `ThresholdKeys::read` as a reachable sink for untrusted bytes) can, with each tiny crafted input:

- Force `Vec::with_capacity(65535)` — a multi-megabyte heap reservation for `C::F` (32+ bytes each), per call, from an input of ~10 bytes. This is an amplification of roughly 10^5× per invocation.
- Repeated calls (e.g., each handshake/key-loading attempt, or iterated parsing of attacker-supplied key material) cause cumulative heap growth / allocator pressure, eventually aborting the process via OOM. On systems with memory limits (containers, validators), this kills the signing/DKG participant — denying service to the threshold signing session, analogous to Puma's thread starvation denying service to unserved connections.

Severity is bounded by the `u16` length cap (max ~2 MB per call on 32-byte fields), so this is a Medium: a real, remotely-reachable resource-exhaustion primitive but requiring repetition to achieve full DoS.

### Likelihood Explanation
`ThresholdKeys::read` is a public API intended for loading serialized threshold keys, and the in-scope rules treat it as an untrusted-byte sink. Triggering requires only: a valid `C::ID` prefix, three `u16`s with `n` large, and interpolation tag `0`. The `with_capacity` allocation executes unconditionally at that point; the following `read_F` failure does not undo it. Reachability depends on the integrator feeding externally-sourced bytes to this function, which is the stated threat model for these `read_*` APIs.

### Recommendation
Validate `n` before allocating:

1. Call `ThresholdParams::new(t, n, i)` (or at minimum check `n != 0`, `n <= t`-style bounds and a hard protocol cap such as `MAX_KEY_SHARES_PER_SET`) immediately after reading `t`, `n`, `i`, before any allocation.
2. Replace `Vec::with_capacity(usize::from(n))` with incremental `push` in the `read_F` loop, so allocation only grows as bytes are actually consumed — the same "only allocate as bandwidth is used" pattern used in `coordinator/src/p2p.rs` (`RrCodec::read_request`) and `networks/ethereum/src/machine.rs` (`Call::read`).

### Proof of Concept
```rust
use std::io::Cursor;
use ciphersuite::Secp256k1;
use dkg::ThresholdKeys;

// Minimal crafted input:
//   id_len (4B) || C::ID || t=1 (2B) || n=65535 (2B) || i=1 (2B) || interpolation=0 (1B)
let mut bytes = Vec::new();
bytes.extend(&(Secp256k1::ID.len() as u32).to_le_bytes());
bytes.extend(Secp256k1::ID);
bytes.extend(&1u16.to_le_bytes());      // t
bytes.extend(&u16::MAX.to_le_bytes());  // n = 65535 -> Vec::with_capacity(65535)
bytes.extend(&1u16.to_le_bytes());      // i
bytes.push(0u8);                        // Interpolation::Constant tag

// Returns Err (input too short for coefficients), but only AFTER a
// ~65535 * size_of::<F>() heap reservation was performed.
let _ = ThresholdKeys::<Secp256k1>::read(&mut Cursor::new(bytes));
// Repeating this call in a loop exhausts memory with ~10 bytes of input each.
```

Each invocation performs the `with_capacity` reservation at `crypto/dkg/src/lib.rs:608` before failing inside the `read_F` loop at line 610 — demonstrating the CWE-400 amplification primitive with no semantic validation of `n` prior to allocation.

### Citations

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
