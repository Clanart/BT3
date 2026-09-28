### Title
Attacker-controlled participant count `n` in `ThresholdKeys::read` drives oversized allocation and unbounded parse loop (excessive memory consumption) - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` trusts a `u16` participant count `n` taken directly from untrusted bytes. Before `ThresholdParams::new` validates `t`/`n`/`i` (which happens only at the very end of the function), the deserializer (a) calls `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` polynomial, (b) loops `n` times calling `C::read_F`, and (c) loops `n` times calling `C::read_G` to fill `verification_shares`. A handful of attacker bytes therefore forces a multi-megabyte allocation and up to 65,535 field/point decode attempts — an allocation-amplification / excessive-memory-consumption bug analogous to CVE-2018-16843's unbounded HTTP/2 memory growth. [1](#0-0) 

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` parses, in order:

1. `C::ID` length + bytes (lines 578–588),
2. `t`, `n`, `i` as raw `u16`s (lines 591–602),
3. an interpolation tag; for tag `0` (`Interpolation::Constant`) it executes `Vec::with_capacity(usize::from(n))` and then `n` calls to `C::read_F` (lines 604–613),
4. a `secret_share` scalar (line 618),
5. a loop over `1 ..= n` inserting `C::read_G` results into a `HashMap` of verification shares (lines 620–623),
6. **only then** `ThresholdParams::new(t, n, i)` (line 626), which is the first place `t <= n` and `i <= n` are checked.

So `n` is fully attacker-controlled and is used to size an allocation and two parse loops *before* any sanity bound is applied. With `n = 0xFFFF`:

- `Vec::with_capacity(65535)` reserves ~2 MiB (for a 32-byte scalar) instantly — triggered by a 2-byte field, i.e. ~10⁶× amplification.
- The `1 ..= n` verification-share loop attempts 65,535 point decodes and `HashMap` insertions; against a `Read` source that yields data (e.g. a stream, or a large posted buffer), memory grows linearly with attacker supply with no cap. There is no upper bound on `n` anywhere in the parse path — `ThresholdParams` only semantically relates `t`, `n`, `i`; it does not impose a protocol maximum.

By contrast, note `Commitments::read` in pedpop sizes its loop from the *caller's* `ThresholdParams` (`crypto/dkg/pedpop/src/lib.rs:111-125`), and `SchnorrAggregate::read` (`crypto/schnorr/src/aggregate.rs:83`) grows its `Vec` incrementally per decoded point without pre-allocation — `ThresholdKeys::read` is the only in-scope parser that both pre-allocates and loops on a raw untrusted count.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (e.g. key material supplied/recovered during DKG orchestration, reshare, or key-import flows) causes each call to allocate multiple MiB and burn up to ~131k group/field decode operations regardless of whether the trailing data is even present — the `with_capacity` allocation happens before the first `read_F` can fail. Repeated or parallel submissions exhaust process memory (OOM kill) or stall the allocator, denying service to the signing/key-management component. This is the same availability impact class as CVE-2018-16843: a small untrusted input produces disproportionate memory consumption in a network-facing parser.

### Likelihood Explanation
Reachability requires an attacker to get crafted bytes into `ThresholdKeys::read`. That is more constrained than an nginx `listen http2` socket — it depends on the integrator passing untrusted serialized `ThresholdKeys` blobs — but the API is a public deserialization entry point intended for loading keys from storage/peer-provided material, and no length or `n`-vs-`t` consistency check exists to reject oversized inputs early. Exploitation is trivial: a ~20-byte prefix with `n = 0xFFFF` suffices to trigger the oversized reservation, so the cost/impact ratio is high whenever the path is reachable.

### Recommendation
- Validate before allocating: reorder `ThresholdKeys::read` to construct `ThresholdParams::new(t, n, i)` immediately after reading the header, and enforce a hard protocol maximum on `n` (e.g. the FROST participant bound) before any `with_capacity`/loops.
- Replace `Vec::with_capacity(n)` with incremental `push` (allocation then tracks bytes actually consumed), or cap the reserve at a small constant.
- Similarly bound the `1 ..= n` verification-share loop by the validated `n`, and consider rejecting `Interpolation::Constant` payloads whose declared `n` exceeds the remaining readable length.
- Apply the same "validate-then-allocate" audit to other `read` entry points (`SchnorrAggregate::read`'s `u32` count, `EncryptedMessage::read`/`EncryptionKeyProof::read` paths) even though they currently lack the pre-allocation amplifier.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — ThresholdKeys::<C>::read, attacker-supplied bytes
let mut bytes = Vec::new();
bytes.extend((C::ID.len() as u32).to_le_bytes());
bytes.extend(C::ID);                       // passes the curve-ID check
bytes.extend(1u16.to_le_bytes());          // t = 1
bytes.extend(0xFFFFu16.to_le_bytes());     // n = 65535  <-- attacker-controlled
bytes.extend(1u16.to_le_bytes());          // i = 1
bytes.push(0u8);                           // Interpolation::Constant tag

// ~20 input bytes -> Vec::with_capacity(65535) reserves ~2 MiB for F,
// then begins 65,535 read_F attempts; the later 1..=n loop would attempt
// 65,535 read_G + HashMap inserts. Params are only validated at line 626,
// after all allocation/parsing is done.
let _ = ThresholdKeys::<C>::read(&mut bytes.as_slice());
```
Each invocation of this ~20-byte payload forces a ~2 MiB reservation plus tens of thousands of decode attempts; issuing it repeatedly/ concurrently from public input channels exhausts memory (DoS) with no secret knowledge or privileged position required.

### Citations

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
