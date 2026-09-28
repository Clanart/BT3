### Title
Attacker-controlled participant count in `ThresholdKeys::read` drives unvalidated allocation and deserialization loop before parameter checking - (File: crypto/dkg/src/lib.rs)

### Summary
The external report describes a memory-consumption DoS in `binutils`' `_bfd_generic_read_minisymbols`, where a crafted input causes the parser to allocate/consume memory proportional to attacker-declared counts rather than actual data. The analogous pattern exists in `ThresholdKeys::read` in `crypto/dkg/src/lib.rs`: the `n` field is read from the byte stream, used to size a `Vec::with_capacity` allocation and to drive `n` iterations of `read_F`/`read_G` deserialization, all before `ThresholdParams::new(t, n, i)` validates the parameters at the end of the function. [1](#0-0) 

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` as raw `u16`s (lines 591–602). It then:

1. Reads an interpolation tag. For `Interpolation::Constant`, it does `Vec::with_capacity(usize::from(n))` and reads `n` scalars (lines 604–613).
2. Reads `n` group elements into a `HashMap` keyed `1 ..= n` (lines 620–623).
3. Only afterwards calls `ThresholdParams::new(t, n, i)` — the first point at which `n`/`t`/`i` are validated for consistency (lines 625–626).

An unprivileged party feeding crafted bytes to `ThresholdKeys::read` (an explicitly supported deserialization path for untrusted data) can set `n = 0xFFFF` (65535), forcing:
- A ~2 MB `Vec::with_capacity` for the constant interpolation variant,
- A `HashMap` grown to 65535 entries with 65535 point deserializations (`read_G` performs full point decoding/validation each iteration — dominant CPU cost),
- All of this work occurs regardless of whether the declared `t`/`n`/`i` combination is valid, since validation is deferred to `ThresholdKeys::new`/`ThresholdParams::new` at the tail of the function.

This is the same bug class as the CVE: resources are committed in proportion to an attacker-supplied count embedded in the input, not to validated protocol state. The byte stream only needs to be large enough to keep `read_exact` succeeding; the CPU cost of 65535 `read_G` invocations and the HashMap/Vec growth is incurred per call, and there is no early bound (e.g., comparing `n` against `t`, or a hard `MAX_PARTICIPANTS` constant) applied before the loops.

Note: I was unable to fully verify every in-scope reader (`crypto/dkg/musig`, `promote`, `recovery`, `dealer` also contain `with_capacity`-based reads that likely share this shape), so this may not be the only instance. `ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) delegates length-prefixed allocation to `rust-bitcoin`'s `consensus_decode`, which carries its own internal cap — weaker than this case.

### Impact Explanation
Repeated submission of short, malformed `ThresholdKeys` payloads (or payloads with maximal `n`) to any endpoint that deserializes them causes sustained CPU burn (point decompression dominates) and transient multi-megabyte allocations per message. In a threshold-signing or DKG-completion flow where other participants' serialized `ThresholdKeys`/`ThresholdCore` blobs are accepted, this enables a remote, unauthenticated resource-exhaustion denial of service — the direct analog of the `nm`-triggered memory-consumption DoS in the CVE.

### Likelihood Explanation
The `read` API is a public, documented deserialization entry point and `n` is attacker-controlled with no pre-validation cap. Exploitation requires only the ability to submit bytes to a deserialization path; no collusion, leaked keys, or protocol violation is needed. The only mitigating factor is that `n` is bounded at 65535 by its `u16` encoding, capping per-message damage — hence Medium rather than High.

### Recommendation
Validate parameters before allocating or looping: call `ThresholdParams::new(t, n, i)` immediately after reading the header, reject `n` above the protocol's real maximum participant count, and prefer incremental `push` over `with_capacity(n)` so allocation grows only with data actually present (the chunked-read approach already used in `networks/ethereum/src/machine.rs` for the same class of issue).

### Proof of Concept
Serialize a `ThresholdKeys` header with a valid `C::ID`, then `t = 1`, `n = 0xFFFF`, `i = 1`, interpolation tag `0` (Constant). `ThresholdKeys::read` immediately reserves capacity for 65535 scalars and begins `read_F`/`read_G` deserialization. Feeding a truncated stream still performs the upfront `Vec::with_capacity(65535)` allocation; feeding a padded stream forces 65535 point decodings plus 65535-entry HashMap growth — orders of magnitude more work than a valid 150-participant set, all before `ThresholdParams::new` can reject anything. Repeat per message for cumulative memory/CPU exhaustion.

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
