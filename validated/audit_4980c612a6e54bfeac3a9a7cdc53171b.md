### Title
Attacker-controlled participant count causes disproportionate heap allocation in `ThresholdKeys::read` - (crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` trusts the serialized `n` field (a `u16`) and immediately executes `Vec::with_capacity(usize::from(n))` when the interpolation byte selects `Interpolation::Constant`, allocating on the order of `n * size_of::<C::F>()` bytes — up to ~2–5 MiB — in response to only ~11 bytes of attacker input, before `ThresholdParams::new` validates `t`, `n`, or `i`. This is the same bug class as ALPINE-CVE-2023-0809 (mosquitto allocating excessive memory based on malicious initial packets): a small, unauthenticated input drives allocation orders of magnitude larger than the input, before any semantic validation.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` deserializes `t`, `n`, and `i` as raw `u16`s, then reads a one-byte interpolation tag. For tag `0` it does:

```rust
0 => Interpolation::Constant({
  let mut res = Vec::with_capacity(usize::from(n));
  for _ in 0 .. n {
    res.push(C::read_F(reader)?);
  }
  res
}),
``` [1](#0-0) 

`n` is fully attacker-controlled (up to 65535). `Vec::with_capacity` reserves `65535 * size_of::<C::F>()` immediately — for a 32-byte scalar that is ~2 MiB, and more for wider fields — before a single scalar byte is read and before `ThresholdParams::new(t, n, i)` (invoked only at the end of the function) can reject `n == 0`, `t > n`, or `i > n`. A truncated input still triggers the full allocation; the `read_exact` failure inside `C::read_F` aborts only *after* the reservation is made. The verification-shares `HashMap` is also filled `n` times (each entry needing real bytes, so bounded — but with hashing/insertion CPU cost proportional to bytes consumed).

Note also that `t` and `n` are not cross-checked at all before the loop: an input `t=1, n=65535, interpolation=0` allocates the full Vec even though `ThresholdKeys::new` later rejects `Constant` interpolation whenever `t != n` — meaning the allocation is provably unreachable by any *valid* key blob with `t != n`, so the reservation happens on input that is guaranteed-invalid.

### Impact Explanation
An unprivileged party that can feed bytes to `ThresholdKeys::read` (per the threat model, untrusted serialized key material) forces an allocation ~1000x+ larger than the bytes sent, per message, with zero validation. Repeated calls — e.g., each time a node ingests a peer-supplied serialized key blob, recover/restore payload, or any RPC/CLI path that round-trips `serialize()`/`read()` — produce continuous multi-megabyte allocate-free churn, enabling memory-amplification and allocator-exhaustion denial of service, mirroring the mosquitto advisory's "excessive memory allocated based on malicious initial packets."

### Likelihood Explanation
Reachability requires a path where deserialized `ThresholdKeys` bytes originate from an untrusted source rather than local storage. The format is self-describing (curve ID + params), and the library exposes `serialize`/`read` as a public wire/storage format, so any integration that accepts key material over the network or from a backup is exposed. The trigger needs only ~11 bytes: valid `C::ID` length + ID, then `t`, `n = 0xFFFF`, `i`, and interpolation byte `0`. Deterministic, no timing or race required.

### Recommendation
Validate parameters *before* allocating: reorder `ThresholdKeys::read` to call `ThresholdParams::new(t, n, i)` immediately after reading `t/n/i`, and reject `Interpolation::Constant` when `t != n` (matching the check in `ThresholdKeys::new`) prior to `Vec::with_capacity`. Prefer incremental `Vec::new()` + `push` over `with_capacity` for untrusted counts, or cap `with_capacity` at the remaining input length. The same audit applies to any other `with_capacity` driven by deserializer-controlled counts.

### Proof of Concept
```rust
// Conceptual PoC against crypto/dkg/src/lib.rs `ThresholdKeys::read`
// C = any ciphersuite, e.g. Ristretto (C::ID == b"ristretto")

let mut input = Vec::new();
input.extend(&(C::ID.len() as u32).to_le_bytes()); // id_len
input.extend(C::ID);                             // id
input.extend(&1u16.to_le_bytes());               // t = 1
input.extend(&u16::MAX.to_le_bytes());           // n = 65535 (attacker-controlled)
input.extend(&1u16.to_le_bytes());               // i = 1
input.push(0);                                   // Interpolation::Constant
// stream ends here — ~15 bytes total

// Vec::with_capacity(65535) executes, reserving ~2 MiB+ for C::F,
// then read_F fails on EOF. Every call burns the allocation.
let _ = ThresholdKeys::<C>::read(&mut input.as_slice());
```

Caveat: I did not fully trace every integration path in-repo to confirm a network-reachable `ThresholdKeys::read` caller; the finding stands on the deserialization API itself, which the threat model lists as an untrusted-byte sink.

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
