### Title
Attacker-controlled participant count in `ThresholdKeys::read` causes excessive memory allocation and denial of service - (File: crypto/dkg/src/lib.rs)

### Summary
The `ThresholdKeys::read` deserializer reads the participant count `n` directly from attacker-supplied bytes and immediately allocates `Vec::with_capacity(usize::from(n))` for the `Constant` interpolation variant, then loops reading `n` scalars and `n` group elements into a `HashMap`. A peer who supplies a crafted serialized `ThresholdKeys` blob can force a ~2–4 MB allocation per message with only ~20 bytes of input, enabling memory-amplification denial of service. This mirrors the reported bug class (memory allocation with excessive size value) present in Serai's own deserialization code.

### Finding Description
`ThresholdKeys::read` in `crypto/dkg/src/lib.rs` parses `t`, `n`, and `i` as little-endian `u16`s from the reader with no sanity bound before allocation [1](#0-0) . When the interpolation tag byte is `0` (`Constant`), it allocates `Vec::with_capacity(usize::from(n))` and reads `n` field elements [2](#0-1) . It then unconditionally loops `(1 ..= n)` reading `n` group elements into a `HashMap` [3](#0-2) . `ThresholdParams::new` is only called after all this work [4](#0-3) .

The same pattern exists elsewhere in in-scope code: `PedPoP`'s `Commitments::read` allocates `Vec::with_capacity(params.t().into())` — that one is defender-bounded — but `ThresholdKeys::read` is the attacker-reachable variant since `n` comes straight from the wire bytes [5](#0-4) .

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (e.g., a peer supplying a serialized key blob during multisig setup/recovery, or any integration deserializing remote `ThresholdKeys`) sets `n = 0xFFFF`. This triggers:

- An immediate `Vec::with_capacity(65535)` for `C::F` elements (~2–4 MB depending on curve) allocated before a single field element is validated.
- A subsequent loop of 65,535 `read_G` calls and `HashMap` insertions before `ThresholdParams::new` is ever invoked to reject `n`.

Each crafted ~15-byte prefix (curve ID length + ID + `t` + `n` + `i` + interpolation tag `0`) forces multi-megabyte allocations. Repeated submissions amplify memory pressure and can exhaust allocator resources, aborting or hanging the process — the exact "allocation with excessive size" DoS class. Only after the reads complete (or EOF errors) does `ThresholdParams::new` reject invalid `n`, so the bound check is too late to prevent the allocation.

### Likelihood Explanation
`ThresholdKeys::read` is a public API in `crypto/dkg` explicitly listed as a sink for untrusted bytes, and `n` is a full `u16` with no bound enforced prior to allocation. No signature, proof, or authentication is required to reach the allocation — just the correct curve `C::ID` prefix. Exploitability is limited to integrations that deserialize `ThresholdKeys` from untrusted sources rather than local storage, which keeps this at Medium rather than High.

### Recommendation
Bound `n` before allocating: reject `n == 0`, `n > MAX_PARTICIPANTS` (e.g., the documented multisig limit, ~a few hundred), or `t > n` immediately after reading the parameters and before `Vec::with_capacity`. Prefer incremental `push` without pre-reserved capacity, or cap capacity at the validated participant count. Apply the same pattern to `EncryptedMessage::read`/`Commitments::read` call sites so params-bounded reads cannot be inflated via a malicious `ThresholdParams`.

### Proof of Concept
```rust
// Feed ThresholdKeys::<Ristretto>::read a blob with:
//   u32 LE C::ID.len() | C::ID | t=1 | n=0xFFFF | i=1 | interpolation=0
// Only ~15 + ID.len() bytes are needed to trigger
// Vec::with_capacity(65535 * sizeof(F)) in crypto/dkg/src/lib.rs:608,
// then 65535 read_G attempts at crypto/dkg/src/lib.rs:621-623
// before ThresholdParams::new is ever called at line 625.
let mut buf = vec![];
buf.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(1u16.to_le_bytes());        // t
buf.extend(0xFFFFu16.to_le_bytes());   // n -> forces ~2-4 MB with_capacity alloc
buf.extend(1u16.to_le_bytes());        // i
buf.push(0);                           // Interpolation::Constant
let _ = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice());
// Each invocation allocates MBs from <20 bytes; looped, this exhausts memory.
```

Uncertainty: I verified the allocation order and lack of bounds in `crypto/dkg/src/lib.rs`; I did not exhaustively check `crypto/frost/src/nonce.rs`, `crypto/dkg/musig`, or `networks/bitcoin` for a larger amplification factor, but `ThresholdKeys::read` is the clearest in-scope reachable path for this bug class.

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

**File:** crypto/dkg/src/lib.rs (L625-631)
```rust
    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```

**File:** crypto/dkg/pedpop/src/lib.rs (L110-127)
```rust
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
