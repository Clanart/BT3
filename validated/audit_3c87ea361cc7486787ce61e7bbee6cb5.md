### Title
Unbounded attacker-controlled allocation and work in `ThresholdKeys::read` driven by a length field before any bytes are verified - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` in `crypto/dkg` (in-scope, and explicitly a reachable sink for untrusted bytes) reads a `u16` participant count `n` from the input stream and immediately sizes allocations and a deserialization loop from it, with no bound tied to the amount of data actually present. This is the Serai analog of CWE-770 (allocation of resources without limits): the declared count is trusted before the input is validated, letting an unprivileged party force memory allocation and expensive group-element decoding work disproportionate to what they send.

### Finding Description
The vulnerable code path is:

```rust
// crypto/dkg/src/lib.rs
let (t, n, i) = { ... read_u16()?, read_u16()?, Participant::new(...)? };
let interpolation = match interpolation[0] {
  0 => Interpolation::Constant({
    let mut res = Vec::with_capacity(usize::from(n));
    for _ in 0 .. n {
      res.push(C::read_F(reader)?);
    }
    res
  }),
  1 => Interpolation::Lagrange,
  _ => ...
};
let mut verification_shares = HashMap::new();
for l in (1 ..= n).map(Participant) {
  verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
}
``` [1](#0-0) 

`n` comes straight from attacker bytes (`u16`, up to 65,535). `Vec::with_capacity(usize::from(n))` reserves ~2 MiB of scalar storage upon seeing as few as ~8 bytes of input, and both the `Constant` interpolation loop and the `verification_shares` loop then attempt `n` field/scalar reads and `n` `read_G` point decompressions/validations respectively. `read_G` performs full curve decoding (canonicality, possibly subgroup/decompression checks) per element — comparatively expensive CPU work. Repeating this call lets an attacker force a validator to burn allocation and point-decoding work with amplification relative to bytes sent in the prefix. Notably, the analogous count-driven loop in the DKG path — `Commitments::read` in `crypto/dkg/pedpop/src/lib.rs` — is bounded by `params.t()` (a local, trusted threshold parameter), not by the input; the defect here is specifically that `ThresholdKeys::read` trusts a *stream-supplied* count. [2](#0-1) 

### Impact Explanation
An attacker who can feed serialized bytes to `ThresholdKeys::read` (listed as a reachable untrusted-bytes sink) can force repeated multi-MiB allocations and up to ~65k curve-point decoding operations per call, amplifying a tiny input into disproportionate memory and CPU consumption. Repeated invocations can exhaust memory or starve the host of CPU, producing a denial of service of the same class as the reported advisory (resource exhaustion via unbounded attacker-controlled sizing in a default processing path).

### Likelihood Explanation
Reachability requires an integration that calls `ThresholdKeys::read` on bytes influenced by an external party; the rules explicitly designate `ThresholdKeys::read` as reachable with untrusted bytes. The trigger is trivial: a short serialized buffer declaring `n = 0xFFFF` followed by truncation still causes the up-front `Vec::with_capacity(65535)` allocation before the first read fails. Caveat: the per-element work stops at EOF, so sustained CPU exhaustion requires either repeatedly invoking the parser or supplying the full ~2 MiB+ of point encodings; allocation amplification, however, applies to even minimal inputs.

### Recommendation
- Validate `n` against a hard upper bound derived from `ThresholdParams` semantics (e.g., reject `n` greater than a protocol maximum, or at minimum `t <= n <= MAX`) before allocating.
- Replace `Vec::with_capacity(n)` with incremental `push` and cap the `verification_shares`/`Constant` loops by a constant bound, so allocation grows only as bytes are actually consumed (the chunked-read pattern already used in `networks/ethereum/src/machine.rs` for the same bug class). [3](#0-2) 
- Check `t`, `n`, and `i` consistency via `ThresholdParams::new` *before* performing any length-dependent allocation or reads.

### Proof of Concept
```rust
// Conceptual: feed a ThresholdKeys<Secp256k1>::read a truncated buffer
// with an inflated participant count.
let mut bytes = vec![];
// Curve ID header matching C::ID (length + ID bytes)
bytes.extend(&(C::ID.len() as u32).to_le_bytes());
bytes.extend(C::ID);
// t = 1, n = 0xFFFF, i = 1
bytes.extend(&1u16.to_le_bytes());
bytes.extend(&u16::MAX.to_le_bytes());
bytes.extend(&1u16.to_le_bytes());
// interpolation = 0 (Constant) -> Vec::with_capacity(65535) before any read
bytes.push(0);
// Truncated: allocation of ~2 MiB occurs, then read_F fails at EOF.
let res = ThresholdKeys::<Secp256k1>::read(&mut bytes.as_slice());
// res errors, but the oversized allocation already happened.
// With interpolation = 1 (Lagrange), the verification_shares loop
// attempts 65,535 read_G point decodings, CPU-bound per element.
```

### Citations

**File:** crypto/dkg/src/lib.rs (L604-623)
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

    let secret_share = Zeroizing::new(C::read_F(reader)?);

    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L109-127)
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
```

**File:** networks/ethereum/src/machine.rs (L51-60)
```rust
    // A valid DoS would be to claim a 4 GB data is present for only 4 bytes
    // We read this in 1 KB chunks to only read data actually present (with a max DoS of 1 KB)
    let mut data = vec![];
    while data_len > 0 {
      let chunk_len = data_len.min(1024);
      let mut chunk = vec![0; chunk_len];
      reader.read_exact(&mut chunk)?;
      data.extend(&chunk);
      data_len -= chunk_len;
    }
```
