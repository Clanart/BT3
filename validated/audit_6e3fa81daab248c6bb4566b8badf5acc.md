### Title
`ThresholdKeys::read` parses an attacker-controlled participant count and performs all deserialization work before validating parameters - ([File: crypto/dkg/src/lib.rs])

### Summary
The upstream advisory concerns `baggage.Parse` iterating the entire attacker-controlled header and fully processing every member before any size/validity limit is enforced. The Serai analog is `ThresholdKeys::<C>::read` in `crypto/dkg/src/lib.rs`, which reads `t`, `n`, and `i` from the raw byte stream, then loops `n` times performing field-element and group-element deserializations, and only afterwards calls `ThresholdParams::new` and `ThresholdKeys::new` to check that `t`/`n`/`i` are actually sane.

### Finding Description [1](#0-0) 

In `ThresholdKeys::read`:

1. `t`, `n`, and `i` are read as raw `u16`s from the reader (lines 591-602). `i` is checked for non-zero via `Participant::new`, but `t` and `n` are used with no validation at all.
2. If the interpolation tag is `0` (`Interpolation::Constant`), it allocates `Vec::with_capacity(n)` and reads `n` field elements via `C::read_F` (lines 607-613).
3. It unconditionally reads `n` group elements into `verification_shares` via `C::read_G` (lines 620-623), each of which performs a full point decoding (including validity/torsion/identity checks that make `read_G` the most expensive per-element step).
4. Only after all `n`-proportional work is done does it call `ThresholdParams::new(t, n, i)` and `ThresholdKeys::new` (lines 625-630), which is where `t <= n`, `n` sanity, and consistency between the verification-share map and `n` are first checked.

So an attacker supplying a byte stream with `n = 0xFFFF` forces 65,535 scalar reads plus 65,535 group decodings (the dominant cost) before the params are rejected. There is no early guard correlating `n` with the remaining input length, and no semantic bound is applied before the loop — exactly the deferred-validation pattern of the advisory (`Parse` fully tokenizing each member before any size enforcement).

The same shape exists in the callers: `processor/src/key_gen.rs` parses each participant's `EncryptedMessage` and feeds `share`/`blame` bytes into `EncryptedMessage::read` / `EncryptionKeyProof::read`, and the blame path even carries a comment acknowledging size-related DoS (lines 535-537), but `ThresholdKeys::read` is the in-scope function (`crypto/dkg`) with the missing upfront bound.

### Impact Explanation
An unprivileged party who can cause a target to run `ThresholdKeys::read` over bytes they control (the rules explicitly list `ThresholdKeys::read` as an untrusted-input sink) can force ~131,070 cryptographic deserialization operations — dominated by `n` full `read_G` point decodings — plus a `Vec`/`HashMap` allocation sized by the attacker's `n`, before the malformed encoding is rejected. Reached repeatedly (e.g., re-submitted key material / blame / share messages that get deserialized on demand), this is a CPU-amplification denial of service against validators or processors. Availability-only impact, bounded by the `u16` count and the transport message size — consistent with Medium severity, matching the advisory's reasoning.

### Likelihood Explanation
Reachability requires an attacker to get their bytes into a `ThresholdKeys::read` call. The in-scope rules designate this function as an untrusted-byte sink, and deserialized key material flows through coordinator/processor message handling where peer-supplied encodings are parsed before semantic checks. The primitive itself — count-prefixed deserialization with post-hoc validation — is unconditionally reachable inside the function once any caller feeds it attacker bytes, with no protocol preconditions.

### Recommendation
Validate `t`, `n`, and `i` via `ThresholdParams::new` (or equivalent bounds checks) immediately after reading them and before allocating or iterating — i.e., move lines 591-602's validation ahead of the `Interpolation`/`verification_shares` loops. Additionally, enforce a relationship between `n` and remaining input length where the reader supports it, and reject `n` values exceeding the maximum the deployment permits before performing any `read_F`/`read_G` work — mirroring the upstream fix of restoring an upfront length check.

### Proof of Concept
Conceptual PoC: construct a byte stream for `ThresholdKeys::<Ristretto>::read` as:

```
C::ID_len (u32 LE, correct) || C::ID || t=0x0001 || n=0xFFFF || i=0x0001 || interpolation=0x01
```

With `interpolation = 1` (`Lagrange`), no `Constant` scalar list is read, but the `verification_shares` loop still attempts 65,535 `read_G` decodings. As long as the buffer supplies enough bytes (65,535 × 32 ≈ 2 MiB), all decodings execute before `ThresholdParams::new`/`ThresholdKeys::new` evaluate the params; even with a truncated buffer, the loop performs as many decodings as bytes allow rather than rejecting `n` upfront. Compare against `n` within valid range: no early rejection path exists in the current code.

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
