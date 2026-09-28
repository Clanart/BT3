### Title
Unauthenticated memory-allocation amplification via attacker-controlled `n` in `ThresholdKeys::read` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes the threshold parameters `t`, `n`, `i` directly from untrusted bytes and immediately trusts `n` to (a) pre-allocate a `Vec` of `n` field elements for `Interpolation::Constant` and (b) drive a `1 ..= n` loop reading `n` verification-share points into a `HashMap`. No sanity bound on `n` is applied before the allocation, mirroring the bug class of CVE-2026-14539 (resource allocation driven by an attacker-supplied length without a defensive limit). [1](#0-0) 

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` reads `t`, `n`, `i` as little-endian `u16`s (lines 591–602), then:

- For `Interpolation::Constant` (tag `0`), it executes `Vec::with_capacity(usize::from(n))` and pushes `n` scalars read via `C::read_F` (lines 607–613).
- It then unconditionally loops `for l in (1 ..= n).map(Participant)` inserting `n` points read via `C::read_G` into a `HashMap` (lines 620–623).

Only *after* this work is done is `n` validated inside `ThresholdParams::new`/`ThresholdKeys::new` (lines 625–631). A 6-byte header (`t=1, n=0xFFFF, i=1`) plus interpolation byte `0` forces an immediate allocation of `65535 * size_of::<C::F>()` (~2–4 MB depending on the ciphersuite's `Repr`), and the `1..=n` loop attempts `65535` point decodes — each `read_G` performing a full decompression plus a re-encode canonicality check (`to_bytes` comparison in `Ciphersuite::read_G`, `crypto/ciphersuite/src/lib.rs:91-100`). The reader errors on EOF, but only after each successfully-presented point was decompressed, so an attacker who supplies actual bytes gets `n` point-decompressions per message; an attacker who supplies nothing still forces the oversized `with_capacity` allocation per invocation.

This is reachable per the stated scope rules: `ThresholdKeys::read` is an explicitly listed read API fed untrusted bytes (it is the deserialization path for multisig keys and is used on processor-received data).

### Impact Explanation
Each malicious invocation from a single tiny input forces a multi-megabyte allocation plus up to 65k expensive point decompression operations before any parameter validation rejects the input. Repeated requests from an unprivileged party cause sustained allocation churn and CPU burn (decompression + canonical re-encoding per claimed share), degrading or terminating the process — the same availability impact (VA:H) as the reference DoS, with amplification because the allocation and per-element crypto work are committed before the declared count is ever checked against `t`/valid ranges.

### Likelihood Explanation
Likelihood is moderate: exploitation requires only the ability to feed attacker-controlled bytes to `ThresholdKeys::read`; no key material, validator status, or collusion is needed. Impact is capped at `u16::MAX` elements per call, so a single call is bounded; effective DoS requires repeated submissions, keeping this at Medium rather than High.

### Recommendation
Validate `t`/`n`/`i` via `ThresholdParams::new` *before* allocating: reject `n` above the protocol's supported maximum and `t > n` prior to `Vec::with_capacity` and the verification-share loop. Prefer pushing into an empty `Vec` (or capping `with_capacity` at a protocol constant) so declared length cannot force allocation ahead of actual data.

### Proof of Concept
Feed `ThresholdKeys::<Secp256k1>::read` the byte sequence `|| C::ID || t=1 || n=0xFFFF || i=1 || 0x00` (Constant interpolation tag) followed by EOF. Line 608 allocates a 65535-element `Vec` of scalars before the first `read_F` fails; omitting the tag and instead supplying `n` crafted-but-valid point encodings forces 65535 decompressions + canonical re-encodes at lines 620–623 before `ThresholdParams::new` at line 626 gets a chance to reject the parameters.

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
