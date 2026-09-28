### Title
Attacker-controlled allocation in `ThresholdKeys::read` enables memory-amplification denial of service - ([File: crypto/dkg/src/lib.rs])

### Summary
CVE-2023-0417 is a resource-exhaustion flaw: a dissector allocates/retains memory proportional to attacker-controlled fields rather than to bytes actually present. The Serai analog lives in `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`), which is explicitly an untrusted-input entry point. After only ~9 bytes of input (curve ID length, curve ID, `t`, `n`, `i`), an interpolation tag byte of `0` causes `Vec::with_capacity(usize::from(n))` at line 608, allocating `n * size_of::<C::F>()` — up to `65535 * 32 = ~2 MiB` — before a single coefficient byte has been read or any parameter validated. Additionally, the `verification_shares` loop at lines 620-623 attempts `n` point reads driven solely by the same unvalidated 2-byte `n` field.

### Finding Description
`ThresholdKeys::read` trusts the serialized `n` (a `u16`) prior to validation: [1](#0-0) 

- `t`, `n`, `i` are read raw at lines 591-602; `ThresholdParams::new` validation only happens at line 626, *after* the allocations.
- With tag `0` (`Interpolation::Constant`), `Vec::with_capacity(n)` reserves up to ~2 MiB for a message that can be as short as 10 bytes — a ~200,000x allocation amplification per crafted input.
- Even with tag `1` (Lagrange), the loop at lines 620-623 iterates `n` times calling `read_G`, so a short buffer still drives 65,535 canonical-point decode attempts (each ~32-byte `read_exact` failing on an empty reader still costs the loop and error-path overhead per iteration).

This mirrors the Wireshark NFS-disseur pattern: memory/CPU work is committed based on a declared length, not on data actually received, letting a tiny crafted payload impose disproportionate cost on the victim.

### Impact Explanation
An unprivileged party who can get a node/coordinator to deserialize `ThresholdKeys` from untrusted bytes (key-exchange/setup messages, or any integrator feeding peer-controlled bytes into `ThresholdKeys::read`) can force repeated multi-megabyte transient allocations and tens of thousands of failed point-deserialization attempts per message. Repeated at scale this produces allocator pressure, GC-like churn, and CPU exhaustion — denial of service of the signing/key-management component — comparable in effect to the CVE's per-packet memory leak (CVSS 6.3, Medium). The allocation is freed on error so it is transient, not a persistent leak, but the amplification per byte sent is large and the cost is paid synchronously in the parsing path.

### Likelihood Explanation
Reachability is real but constrained: impact requires an integrator that pipes peer/network-controlled bytes into `ThresholdKeys::read` on an unauthenticated channel, which is a documented use (it is a `pub fn` reading from `io::Read`, listed among the in-scope untrusted-byte entry points). The per-message amplification (~2 MiB from a 10-byte claim for the `Constant` path) makes sustained pressure cheap for the attacker. Severity is Medium: availability impact only, no secret leakage, and the effect is bounded per call (~2 MiB + `n` iterations) rather than unbounded.

### Recommendation
Validate parameters before allocating: read `t`, `n`, `i` and immediately construct `ThresholdParams::new(t, n, i)` (rejecting `t == 0`, `n == 0`, `t > n`, `i > n`) before the interpolation tag is handled. For `Interpolation::Constant`, use `Vec::new()` + `push` per successfully read scalar instead of `Vec::with_capacity(n)`, or cap `n` at a protocol-plausible maximum (the real validator-set size) prior to any allocation. Optionally bound total bytes consumed against the declared `n` (each share requires `F::Repr`/`G::Repr` bytes, so a stream shorter than `header + n*repr` can be rejected up front).

### Proof of Concept
Conceptual byte stream fed to `ThresholdKeys::<C>::read`:

```
[u32 LE: C::ID.len()] [C::ID bytes]          // passes the curve-ID check
[u16 LE: t = 1] [u16 LE: n = 0xFFFF] [u16 LE: i = 1]
[u8: 0]                                       // Interpolation::Constant tag
// EOF — no further bytes needed
```

At `crypto/dkg/src/lib.rs:608` this triggers `Vec::with_capacity(65535)` (~2 MiB allocation), followed by 65,535 `read_F` calls that all fail on the empty reader — and then `ThresholdParams::new` is never even reached for validation because the error surfaces first. Repeating this minimal payload forces sustained multi-megabyte allocation churn per ~15 bytes of attacker input. For the Lagrange path (tag `1`), the same prefix drives 65,535 `read_G` iterations. No valid shares, signatures, or keys are required — only the ability to supply bytes to `ThresholdKeys::read`.

### Citations

**File:** crypto/dkg/src/lib.rs (L604-616)
```rust
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
