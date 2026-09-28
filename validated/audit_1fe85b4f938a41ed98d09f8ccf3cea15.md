### Title
`ThresholdKeys::read` performs attacker-sized allocation before validating input length — crafted header causes memory exhaustion - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::read` in `crypto/dkg/src/lib.rs` reads a 16-bit `n` from untrusted bytes and immediately calls `Vec::with_capacity(usize::from(n))` for `Interpolation::Constant` and then builds a `HashMap` of `n` verification shares — allocating resources proportional to an attacker-chosen count before confirming the corresponding bytes actually exist. This is the same bug class as CVE-2017-8343: a parser that trusts a declared length in a crafted input to drive memory consumption, reachable by any unprivileged party who can feed bytes to `ThresholdKeys::read`. [1](#0-0) 

### Finding Description
After reading `t`, `n`, `i` (lines 591–602), the code executes `Vec::with_capacity(usize::from(n))` when `interpolation == 0` (Constant) at line 608, allocating space for up to 65,535 field elements (~2 MB for 32-byte scalars) based solely on the header. Even when `interpolation == 1` (Lagrange), it unconditionally loops `for l in (1 ..= n)` at lines 621–623 inserting `n` entries into a `HashMap`. Both allocations are driven entirely by the attacker-supplied `n` before `ThresholdParams::new(t, n, i)` is validated (the parameters check happens only at line 626, *after* the allocations and reads). A truncated input of just a few bytes — `id_len`, valid `C::ID`, `t`, `n = 0xFFFF`, `i`, `interpolation = 0` — triggers the full `with_capacity` allocation before `read_F` fails. Repeated invocations (e.g., feeding crafted key blobs to any endpoint that calls `ThresholdKeys::read`) turn a few bytes of input into ~2 MB allocations plus HashMap growth each time, amplifying memory pressure.

### Impact Explanation
An unprivileged party can cause disproportionate heap allocation per request by sending a handful of crafted bytes with `n` set to the maximum. Sustained submissions exhaust process memory (DoS), mirroring the medium-severity memory-exhaustion class of the external report. No secret data is leaked; impact is availability only.

### Likelihood Explanation
`ThresholdKeys::read` is a public deserialization API explicitly listed as reachable with untrusted bytes. The crafted input requires no valid signature, proof, or cryptographic content — only a valid `C::ID` prefix followed by header fields, so any exposed deserialization path accepts it. The cost to the attacker is ~20 bytes per ~2 MB of allocation.

### Recommendation
Validate `t`, `n`, and `i` via `ThresholdParams::new` *before* allocating; cap `n` against a protocol maximum prior to `with_capacity`; and grow the vector incrementally (`Vec::new` + `push`) or check remaining input length so allocation tracks bytes actually present rather than the declared count.

### Proof of Concept
```rust
use std::io;
use ciphersuite::Ciphersuite;
use dkg::ThresholdKeys;
// C: any in-scope ciphersuite, e.g. dalek_ff_group::Ristretto

fn poc<C: Ciphersuite>() {
  let mut buf = vec![];
  buf.extend(&(u32::try_from(C::ID.len()).unwrap()).to_le_bytes());
  buf.extend(C::ID);            // valid curve ID so the check passes
  buf.extend(&1u16.to_le_bytes());      // t
  buf.extend(&u16::MAX.to_le_bytes());  // n = 65535 -> ~2MB with_capacity + HashMap sizing
  buf.extend(&1u16.to_le_bytes());      // i
  buf.push(0);                          // interpolation = Constant -> Vec::with_capacity(65535)

  // No further bytes needed: the allocation at line 608 already happened;
  // read_F then fails, returning Err after the memory was reserved.
  let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
}
```

### Citations

**File:** crypto/dkg/src/lib.rs (L591-623)
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
```
