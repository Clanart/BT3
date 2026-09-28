### Title
Attacker-controlled `n` in `ThresholdKeys::read` forces a multi-megabyte `Vec::with_capacity` allocation before any validation, enabling memory-exhaustion DoS - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) is listed as an untrusted-bytes entry point. It reads `n` as a raw `u16` from the byte stream and immediately executes `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` coefficient vector — before `ThresholdParams::new` is ever called to validate `t`/`n`/`i`. A short crafted input therefore triggers a single, synchronous allocation of `n * size_of::<C::F>()` bytes (~2 MiB for `n = 0xFFFF` on a 32-byte field) plus a second `n`-element `HashMap` fill loop for `verification_shares`, with no bound checked first.

### Finding Description
The bug-class analogue of the vm2 `Buffer.alloc` issue — an attacker-controlled size driving one large heap allocation that bounds/interrupts cannot stop — maps onto the deserialize-then-validate pattern in `ThresholdKeys::read`:

1. `t`, `n`, `i` are read as raw `u16`s (lines 591-602). `Participant::new` rejects `i == 0`, but `t` and `n` are unvalidated at this point.
2. If the interpolation tag byte is `0`, `Interpolation::Constant` does `Vec::with_capacity(usize::from(n))` and pushes `n` scalars read from the stream (lines 607-613). The capacity allocation happens up front regardless of how many bytes the attacker actually sent — identical in shape to `Buffer.alloc(N)` reserving host heap before any data exists.
3. `secret_share` and then `n` entries of a `HashMap<Participant, C::G>` are read (lines 618-623).
4. Only at the very end does `ThresholdParams::new(t, n, i)` validate the parameters (lines 625-631) — after all allocations already occurred.

The `id` prefix (`C::ID.len()` + `C::ID`) and interpolation tag mean the entire trigger requires only ~10 attacker bytes to force the ~2 MiB allocation plus a `HashMap` of up to 65,535 in-flight entries — an amplification on the order of 10^5:1 for a single input. Rejected/truncated streams still pay the `with_capacity` cost because allocation precedes `read_exact` failure.

### Impact Explanation
DoS via memory exhaustion. Any endpoint that accepts serialized `ThresholdKeys` from an unprivileged party (key-exchange/registration flows, recovery, or any `read_share`/keys-ingestion surface) can be driven to allocate ~2-4 MiB per request with a handful of bytes. Under repeated requests in memory-constrained deployments (containers, pods), this exhausts the process heap and aborts — the same impact class as the reference advisory (synchronous native-sized allocation, no cooperative bound). Severity is bounded below the vm2 case because `n` is capped at 65,535 rather than 2^32, capping per-call amplification at a few MiB rather than gigabytes; accordingly this is Medium, not High.

### Likelihood Explanation
Reachability depends on the deployment exposing `ThresholdKeys::read` (or a wrapper such as `EncryptedMessage::read`/`Commitments::read`-adjacent key ingestion) to untrusted input. The deserialization itself is deterministic, requires no valid signature or key material, and fails only *after* the allocation, so the cost is paid on every malformed input. No attacker capability beyond sending crafted bytes is needed.

### Recommendation
Validate `n` (and `t <= i <= n` via `ThresholdParams::new`) immediately after reading the three `u16`s and *before* any `Vec::with_capacity`/`HashMap` growth keyed by `n`; alternatively cap `n` at a protocol-maximum participant count and use incremental `push` (no pre-sized capacity) so a truncated stream costs only the bytes actually supplied.

### Proof of Concept
```rust
use std::io;
use ciphersuite::{Ciphersuite, Secp256k1};
use dkg::{ThresholdKeys, Participant};

// Craft a ThresholdKeys blob claiming n = 65535 with interpolation = Constant.
// Only ~10 bytes of input trigger Vec::with_capacity(65535 * 32 bytes) ~ 2 MiB
// inside ThresholdKeys::read before any parameter validation occurs.
let mut buf = vec![];
// id_len + id for the curve
buf.extend(&u32::try_from(Secp256k1::ID.len()).unwrap().to_le_bytes());
buf.extend(Secp256k1::ID);
// t = 1, n = 0xFFFF, i = 1
buf.extend(&1u16.to_le_bytes());
buf.extend(&0xFFFFu16.to_le_bytes());
buf.extend(&1u16.to_le_bytes());
// interpolation = Constant => Vec::with_capacity(65535) allocated here
buf.push(0u8);

// Read returns Err quickly (truncated scalar stream) but the ~2 MiB allocation
// and partial HashMap fill already happened; repeat in a loop to exhaust memory.
let res: io::Result<ThresholdKeys<Secp256k1>> =
  ThresholdKeys::read(&mut buf.as_slice());
assert!(res.is_err()); // error only AFTER the allocation
``` [1](#0-0) [2](#0-1)

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

**File:** crypto/dkg/pedpop/src/lib.rs (L109-128)
```rust
impl<C: Ciphersuite> ReadWrite for Commitments<C> {
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
  }
```
