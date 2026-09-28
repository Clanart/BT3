### Title
Unbounded attacker-controlled allocation in `ThresholdKeys::read` via unvalidated participant count `n` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` trusts a 2-byte `n` field read from untrusted input to size allocations and loop counts *before* any validation of `n` occurs. Analogous to the referenced Mattermost advisory (unbounded request body → resource exhaustion), a short serialized prefix causes the reader to allocate and attempt to decode up to `u16::MAX` field elements and `u16::MAX` group elements, yielding a large memory/CPU amplification factor per call and enabling memory-exhaustion DoS wherever untrusted bytes reach `ThresholdKeys::read` (e.g., multisig/key material handled by processors).

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` parses `t`, `n`, `i` as raw `u16`s, then immediately:

- allocates `Vec::with_capacity(usize::from(n))` and loops `for _ in 0 .. n` calling `C::read_F(reader)` for `Interpolation::Constant` (lines 604–616),
- loops `for l in 1 ..= n` calling `C::read_G(reader)` and inserting into a `HashMap` (lines 620–623),

all **before** `ThresholdParams::new(t, n, i)` performs any bounds/consistency checks at line 625–631 (`ThresholdParams::new` itself, lines 166–179, only rejects `n == 0` and `t > n` — `n` up to 65535 is accepted).

Since `n` is attacker-controlled and only 2 bytes, each call forces up-front allocation of `n * size_of::<C::F>()` plus `n` `read_G`/`read_F` decode operations. For `Interpolation::Constant`, the `Vec::with_capacity(n)` allocation happens *before* a single coefficient byte is read, so a ~10-byte header triggers up to ~2–4 MB of allocation (e.g., `65535 × 57` bytes for ed448 `F::Repr`, `65535 × 32` for 25519 scalars) regardless of how much data follows. There is no upper bound tying `n` to the remaining input length, and no chunk-limited read like the mitigation pattern used in `networks/ethereum/src/machine.rs` `Call::read` (which explicitly reads claimed length in 1 KB chunks to cap the DoS). Repeating the call on a stream trivially amplifies attacker bandwidth into heap growth → OOM/abort of the process handling the deserialization. [1](#0-0) [2](#0-1) 

### Impact Explanation
An unprivileged party who can cause a processor/node to deserialize attacker-supplied bytes via `ThresholdKeys::read` (a listed untrusted-bytes sink) can force multi-megabyte allocations and `O(n)` expensive field/group decoding from a handful of input bytes. Repeated submissions exhaust memory or stall the process — resource exhaustion / denial of service, matching the CWE-770 class of the reference advisory. No secret material is required; only reachability of the `read` path with attacker bytes is needed.

### Likelihood Explanation
Reachable wherever serialized `ThresholdKeys` are accepted over the wire or from peer-supplied messages. The trigger is trivial: a valid `C::ID` header followed by `n = 0xFFFF` and interpolation tag `0` (Constant) forces the maximum allocation with minimal input. Exploitation requires only the ability to submit repeated messages — no threshold, collusion, or key knowledge. Constrained to a memory/CPU DoS (no forgery or key recovery), consistent with Medium severity.

### Recommendation
Bound `n` before allocating: read `n` into a `u16`, then enforce `n` against a protocol maximum (e.g., the real validator-set cap) and/or the remaining input length before `Vec::with_capacity`. Prefer pushing elements as they're successfully decoded (streaming) rather than pre-allocating from the claimed count — the same mitigation `Call::read` in `networks/ethereum/src/machine.rs:51-60` documents ("claim a 4 GB data is present for only 4 bytes... read this in 1 KB chunks"). Alternatively, validate `ThresholdParams` immediately after reading `t`, `n`, `i` and reject `n` above the deployment's maximum participant count.

### Proof of Concept
```rust
use std::io::Cursor;
use ciphersuite::Ciphersuite;
use dkg::ThresholdKeys;
// Any in-scope ciphersuite, e.g. kp256/ed25519 dalek:
// use ciphersuite::Ed25519 as C;

fn poc<C: Ciphersuite>() {
    let mut bytes = vec![];
    // C::ID header (length-prefixed, must match)
    bytes.extend(&(C::ID.len() as u32).to_le_bytes());
    bytes.extend(C::ID);
    // t = 1, n = u16::MAX, i = 1
    bytes.extend(&1u16.to_le_bytes());
    bytes.extend(&u16::MAX.to_le_bytes());
    bytes.extend(&1u16.to_le_bytes());
    // interpolation = Constant -> Vec::with_capacity(65535) allocated NOW,
    // before a single coefficient byte is present in the stream
    bytes.push(0u8);
    // no coefficient bytes follow at all

    let mut cursor = Cursor::new(&bytes);
    // Allocates ~n * size_of::<C::F>() on the heap, then fails on read_F —
    // but the amplification already happened. Repeat over a stream → OOM.
    let _ = ThresholdKeys::<C>::read(&mut cursor);
}
```

Each invocation converts ~12 bytes of input into a multi-megabyte heap allocation; looping it on a connection that feeds `ThresholdKeys::read` exhausts memory, mirroring the oversized-request-body DoS in the Mattermost report.

Note: I was unable to inspect `crypto/dkg/pedpop/src/encryption.rs` (`EncryptedMessage::read`) within the available iterations; if it also reads attacker-controlled lengths without a bound, it would be an additional instance of the same class.

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
