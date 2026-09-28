### Title
Unchecked attacker-controlled participant count drives allocation and read loops in `ThresholdKeys::read` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` in `crypto/dkg` deserializes `t`, `n`, and `i` from an untrusted byte stream and immediately uses `n` to size a `Vec::with_capacity` and to bound two read loops — before `ThresholdParams::new` validates the values at the end of the function. This is the same defect class as CVE-2025-43801 (unchecked input used for a loop condition): a small crafted buffer causes an allocation and loop trip count of up to 65,535, yielding asymmetric resource consumption for an unprivileged remote party feeding bytes to `ThresholdKeys::read`.

### Finding Description
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574), the header fields are read directly from the reader:

- `t`, `n`, `i` are parsed as raw `u16`s at crypto/dkg/src/lib.rs:591-602 with no bound check at parse time.
- If the interpolation byte is `0` (Constant), `Vec::with_capacity(usize::from(n))` executes at crypto/dkg/src/lib.rs:608 and a loop `for _ in 0 .. n` calls `C::read_F` at crypto/dkg/src/lib.rs:609-611.
- Independently of the interpolation variant, `for l in (1 ..= n).map(Participant)` calls `C::read_G` per iteration at crypto/dkg/src/lib.rs:620-623.
- Only after all of this work is `ThresholdParams::new(t, n, i)` invoked at crypto/dkg/src/lib.rs:625-626 to validate the parameters.

Because `n` is a `u16` fully controlled by the input, a crafted buffer can set `n = 65535`. The `with_capacity` call alone allocates `n * size_of::<C::F>()` (~2 MB for a 32-byte scalar) in response to a buffer that can be only ~9 bytes long — an allocation amplification on the order of 10^5x per call. With `Interpolation::Constant` selected, the same tiny input additionally forces a `Vec` growth loop, and the verification-shares loop performs up to 65,535 `read_G` attempts (each a point decompression when bytes are supplied). All of this occurs before parameter validation, so invalid inputs still consume the resources.

### Impact Explanation
`ThresholdKeys::read` is on the listed reachable deserialization surface for untrusted bytes. An unprivileged attacker who can submit crafted serialized key material (e.g., via a signing/DKG recovery or message path that deserializes `ThresholdKeys`) forces the victim to perform large attacker-dictated heap allocations and decompression loops per message. Repeated submissions amplify CPU and memory pressure disproportionately to bandwidth spent, degrading or denying service — a remote, unauthenticated DoS matching the source advisory's impact (VA:L, Medium).

### Likelihood Explanation
The trigger requires only reaching `ThresholdKeys::read` with attacker-controlled bytes — no valid signature, proof, or trusted state is needed since the loop and allocation precede all validation (`ThresholdParams::new` runs last). Cost to the attacker is a handful of bytes per attempt; exploitation is deterministic, not probabilistic. Likelihood is bounded only by whether an exposed endpoint feeds attacker bytes into this reader, which the stated reachability assumptions grant.

### Recommendation
Validate `t`/`n`/`i` before any allocation or loop: construct and check `ThresholdParams::new(t, n, i)` immediately after reading the header at crypto/dkg/src/lib.rs:591-602, reject `n`/`t` exceeding protocol-sane bounds, and then size allocations from the validated `params.n()` rather than the raw wire value.

### Proof of Concept
```text
serialized = u32_le(len(C::ID)) || C::ID ||
             t = 0xFFFF || n = 0xFFFF || i = 0x0001 ||
             interpolation = 0x00   // Constant
             // (input ends here — ~9 + len(C::ID) bytes total)

ThresholdKeys::<C>::read(&mut serialized.as_ref())
// -> Vec::with_capacity(65535) allocates ~2 MB before the first read_F fails on EOF
// With interpolation = 0x01 (Lagrange), the 1..=n read_G loop still runs 65535 iterations
// threshold-param validation is only reached after all reads succeed
``` [1](#0-0)

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
