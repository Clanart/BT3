### Title
Unbounded attacker-controlled allocation in `ThresholdKeys::read` via participant count - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to CVE-2023-3614 (specially crafted input causing disproportionate resource consumption), `ThresholdKeys::read` in the in-scope `crypto/dkg` crate trusts an attacker-controlled `n` field and immediately pre-allocates a `Vec` of `n` field elements (`Interpolation::Constant`) — and later drives a `HashMap`/`read_G` loop over `1 ..= n` — before a single scalar or point byte has been consumed. A 2-byte length field forces a multi-megabyte allocation, giving a large input-to-work amplification factor reachable through any path that feeds untrusted bytes into `ThresholdKeys::read` (an explicitly permitted reachability surface).

### Finding Description
In `crypto/dkg/src/lib.rs` (`ThresholdKeys::read`, lines 591–633), after reading the curve ID, the code reads `t`, `n`, and `i` as raw `u16`s:

```rust
// crypto/dkg/src/lib.rs:591-602
let (t, n, i) = {
  let mut read_u16 = || -> io::Result<u16> {
    let mut value = [0; 2];
    reader.read_exact(&mut value)?;
    Ok(u16::from_le_bytes(value))
  };
  (read_u16()?, read_u16()?,
   Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?)
};
```

`n` is then used unchecked to size an allocation:

```rust
// crypto/dkg/src/lib.rs:606-613
0 => Interpolation::Constant({
  let mut res = Vec::with_capacity(usize::from(n));
  for _ in 0 .. n {
    res.push(C::read_F(reader)?);
  }
  res
}),
```

`Vec::with_capacity(65535)` commits roughly `n * size_of::<C::F>()` (~2 MiB for a 32-byte scalar) the instant the interpolation tag `0` is parsed — before `read_F` ever touches the reader and before `ThresholdParams::new(t, n, i)` can reject `n` (validation happens only at line 625–626, after the allocation and the `n`-element `read_G` loop at lines 620–623). Unlike length-prefixed blob reads where the attacker must supply the claimed bytes, here the allocation is performed upfront, so a ~40-byte crafted input forces a ~2 MiB allocation and an early `Err` return. The caller frees the buffer on error, but the transient allocation still occurs, and an attacker issuing many concurrent/serialized `read` calls (or reaching a `Vec<ThresholdKeys>`/`HashMap<_, ThresholdKeys>` deserialization path such as multisig-set storage/network decoders) multiplies the amplification per request.

The same pattern exists wherever the in-scope `dkg`-family deserializers trust a participant/count `u16` before bounds-checking it against `ThresholdParams`, e.g. `musig`'s `Vec::with_capacity(keys.len())`/`HashMap::with_capacity` calls in `crypto/dkg/musig/src/lib.rs:140-142` follow a caller-controlled key list, and `SchnorrAggregate::read` in `crypto/schnorr/src/aggregate.rs:77-88` accepts a `u32` count with no sanity bound (though there the allocation grows only as elements are actually read).

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ThresholdKeys::read` (or the other listed `read`/`deserialize` entry points) can cause a small request to trigger a disproportionately large heap allocation — the exact bug class of the reference CVE. Repeated requests exhaust memory or cause allocator pressure/OOM termination of the node process, a network/CPU-amplified denial of service. Severity is Medium: no secret material or correctness impact, only availability.

### Likelihood Explanation
Reachability depends on integrators deserializing `ThresholdKeys`/`ThresholdParams`-containing structures from untrusted transport. The scope rules explicitly treat bytes fed to `ThresholdKeys::read` as attacker-reachable, and the trigger requires only ~40 bytes with a plausible `C::ID`, `n = 0xFFFF`, and interpolation tag `0`. No key material, collusion, or validator status is needed.

### Recommendation
Validate `t`, `n`, and `i` with `ThresholdParams::new` **before** allocating; reject `n` above the protocol's real maximum validator-set size (a small constant, well below `u16::MAX`); replace `Vec::with_capacity(n)` with an incremental `Vec::new()` + push loop so allocation tracks bytes actually consumed; and apply the same bound to the `1 ..= n` `read_G`/`HashMap` loop and to `u32` counts in `SchnorrAggregate::read`.

### Proof of Concept
```rust
// For a Ciphersuite C with a known C::ID (e.g. Ristretto, C::ID = b"ristretto"):
let mut bytes = vec![];
bytes.extend((C::ID.len() as u32).to_le_bytes());
bytes.extend(C::ID);            // passes the curve-ID check
bytes.extend(1u16.to_le_bytes()); // t
bytes.extend(u16::MAX.to_le_bytes()); // n = 65535
bytes.extend(1u16.to_le_bytes()); // i = 1 (valid Participant)
bytes.push(0);                  // Interpolation::Constant -> Vec::with_capacity(65535)
// ThresholdKeys::<C>::read(&mut &bytes[..]) now allocates ~2 MiB of C::F
// before read_F hits EOF and errors out. Each such ~50-byte input burns ~2 MiB
// of allocator work; looped, it starves the allocator / OOMs the process.
```

Uncertainty note: line-level contents of `crypto/dkg/pedpop/src/lib.rs` and `crypto/frost/src/sign.rs` were not fully retrieved in the available iterations, so equivalent unchecked-count allocations in `EncryptedMessage::read` / `Commitments::read` / `read_preprocess` could not be confirmed; the `ThresholdKeys::read` instance stands on its own as the proven analog.