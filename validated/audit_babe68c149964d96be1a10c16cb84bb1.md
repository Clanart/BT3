### Title
Attacker-controlled participant count in `ThresholdKeys::read` forces unbounded deserialization work before validation - (File: crypto/dkg/src/lib.rs)

### Summary
The external report (CVE-2022-27406) is a crash where an attacker-influenced size parameter (`FT_Request_Size`) drives unsafe work. In Serai's in-scope DKG code, `ThresholdKeys::read` reads the threshold parameters `t`, `n`, and `i` directly from an untrusted byte stream and then uses the attacker-supplied `n` to drive allocation and per-element deserialization (scalars and curve points) *before* `ThresholdParams::new` / `ThresholdKeys::new` ever validate the values.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, and `i` as raw `u16`s from the reader at `crypto/dkg/src/lib.rs:591-602`. It then:

1. If interpolation byte is `0` (`Interpolation::Constant`), allocates `Vec::with_capacity(usize::from(n))` and reads `n` field elements via `C::read_F` (lines 607-613).
2. Reads `n` group elements via `C::read_G` into `verification_shares` (lines 620-623), each of which performs a full point decompression/validation.
3. Only afterwards calls `ThresholdParams::new(t, n, i)` and `ThresholdKeys::new(...)`, which is the first place `t <= n`, `n` bounds, share counts, and interpolation applicability (e.g. Constant requires `t == n`, checked at `ThresholdKeys::new`, lines 368-372) are enforced (lines 625-631).

So the amount of deserialization work — up to 65,535 scalar reads plus 65,535 curve point decompressions — is governed entirely by the attacker-supplied `n`, not by the validator's real parameters. `n` is never checked against the actual `ThresholdParams` of the receiving party before being used. This is reachable by an unprivileged party feeding crafted bytes to `ThresholdKeys::read` (an explicitly in-scope attacker surface for serialized key material).

### Impact Explanation
Availability impact, mirroring the CVE's `A:H`. A single malicious serialized `ThresholdKeys` blob forces the victim to perform tens of thousands of curve point decompressions and field element reads before rejecting the input — repeated submissions cause sustained CPU exhaustion of the node's DKG/key-loading path. Because `n` is unvalidated, the work performed bears no relation to the legitimate validator set size (typically far smaller). This is a denial of service reachable purely with public input bytes; no collusion or key material is required.

### Likelihood Explanation
Any unprivileged party who can deliver bytes to a `ThresholdKeys::read` consumer (key-share exchange, reshare/recovery flows, or any integrator that loads peer-supplied `ThresholdKeys`) can set `n = 0xffff` and append ~4 MB of well-formed scalar/point encodings to trigger maximum work. No authentication or threshold participation is needed, and malformed tails still force all prior reads/decompressions to execute before the size is checked.

### Recommendation
Bound `n` before using it: after reading `t`, `n`, `i`, immediately validate `n` against a protocol maximum (and/or the expected `ThresholdParams` where known, e.g. require `n` to equal the validator set size), reject `n == 0`, and require `t <= n` *before* allocating `Vec::with_capacity(n)` or entering the `0 .. n` read loops. Apply the same pattern anywhere else a length field is read ahead of validation.

### Proof of Concept
```rust
// Attacker crafts a serialized ThresholdKeys blob:
//   id_len = C::ID.len() (correct, to pass the curve check)
//   id     = C::ID
//   t = 0x0001, n = 0xffff, i = 0x0001   // attacker-chosen n = 65535
//   interpolation = 0x00 (Constant)
//   then 65535 valid C::F encodings, one C::F secret share,
//   and 65535 valid C::G encodings
//
// Victim: ThresholdKeys::<C>::read(&mut blob)
//  - Vec::with_capacity(65535) allocated (dkg/src/lib.rs:608)
//  - 65535 read_F calls (lines 609-611)
//  - 65535 read_G point decompressions (lines 621-623)
//  - Only then does ThresholdParams::new / ThresholdKeys::new validate (line 626)
// Repeated submissions keep the victim CPU-bound on decompression.
```

Root cause evidence: `n` is read unvalidated at [1](#0-0) , drives `Vec::with_capacity(n)` and `n` scalar reads at [2](#0-1) , drives `n` point decompressions at [3](#0-2) , and is only validated afterward in `ThresholdParams::new`/`ThresholdKeys::new` at [4](#0-3) .

Caveat: the work per message is bounded (~65k decompressions, a few MB), so severity is Medium rather than the CVE's unbounded-segfault High; I could not fully confirm whether downstream callers impose an outer size limit, which would reduce impact further.

### Citations

**File:** crypto/dkg/src/lib.rs (L591-602)
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
```

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

**File:** crypto/dkg/src/lib.rs (L620-623)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```

**File:** crypto/dkg/src/lib.rs (L625-632)
```rust
    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
  }
```
