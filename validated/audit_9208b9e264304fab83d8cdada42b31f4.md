### Title
Untrusted length prefix in `ReceivedOutput::read` enables attacker-triggered memory-exhaustion DoS - (File: crypto/../networks/bitcoin/src/wallet/mod.rs)

### Summary
The bug class behind CVE-2022-24130 is "attacker-controlled input length drives an oversized buffer operation" (a length-derived buffer overflow in `set_sixel`). Serai's analog lives in the untrusted deserialization path `ReceivedOutput::read`, which hands a raw byte stream directly to Bitcoin consensus decoders that allocate buffers sized by attacker-controlled VarInt/length prefixes, with no size cap enforced by bitcoin-serai.

### Finding Description
`ReceivedOutput::read` reads a scalar offset and then calls `TxOut::consensus_decode` and `OutPoint::consensus_decode` on whatever bytes remain: [1](#0-0) 

`TxOut` consensus decoding includes the `script_pubkey`, whose serialized form is `VarInt(len) || bytes`. The `rust-bitcoin` `Decodable` implementation allocates a buffer proportional to the declared `len` before/while reading the payload, so a stream declaring e.g. a 4 GB (or larger, up to u64) script causes an allocation of that size while the actual payload can be a few bytes. There is no pre-check in `ReceivedOutput::read` bounding the remaining input size — nothing like the chunked-read defense that `Call::read` in the Ethereum network code explicitly added to mitigate exactly this "claim 4 GB, send 4 bytes" DoS: [2](#0-1) 

The same pattern recurs elsewhere in the untrusted `read_*` surface: `SchnorrAggregate::read` loops `u32::from_le_bytes(len)` times with no bound on the claimed count beyond what the reader supplies, and `ThresholdKeys::read` lets the attacker pick `n` (u16) and then allocates `Vec::with_capacity(n)` and reads `n` scalars plus `n` group elements: [3](#0-2) [4](#0-3) 

### Impact Explanation
Per the scan rules, `ReceivedOutput::read` and `ThresholdKeys::read` are reachable with attacker-supplied bytes. A single malformed serialized `ReceivedOutput` (or any structure embedding one, e.g. `Output::read` → `ReceivedOutput::read`) containing a TxOut whose `script_pubkey` VarInt declares an enormous length forces a multi-GB allocation / OOM abort of the host process. This is an unauthenticated, single-message availability kill on any component deserializing such outputs — analogous to the A:H impact of the xterm CVE. Severity is medium: DoS only, no secret leakage or forgery.

### Likelihood Explanation
Exploitation requires only delivering crafted bytes to a code path that calls `ReceivedOutput::read` (or `Plan`/`Output` deserialization which embeds it). No keys, no collusion, no protocol participation is needed — just a malformed byte stream. The cost to the attacker is a handful of bytes; the cost to the victim is process death. Likelihood is bounded only by how often these blobs cross a trust boundary (disk-reload paths and inter-service messages both qualify).

### Recommendation
- In `ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122), wrap the reader in a bounded adapter (`io::Read::take`) sized to a sane maximum for a TxOut + OutPoint (e.g. 10 KB, or consensus `MAX_STANDARD_TX_SIZE`-bounded), so consensus_decode cannot allocate from an unchecked VarInt.
- Apply the same defense to the other length-prefixed reads: cap `n`/`len` fields in `ThresholdKeys::read` (crypto/dkg/src/lib.rs:591) against `u16` bounds already implied by `ThresholdParams`, and cap the `Rs` count in `SchnorrAggregate::read` (crypto/schnorr/src/aggregate.rs:83) against remaining-input size.
- Mirror the 1 KB chunked-read pattern already used in `Call::read` (networks/ethereum/src/machine.rs:54) wherever variable-length attacker-controlled data is decoded.

### Proof of Concept
```rust
// Construct a serialized ReceivedOutput whose TxOut claims a ~4 GiB script_pubkey.
let mut buf = vec![];
buf.extend([0u8; 32]);           // offset scalar (any canonical F)
buf.extend(0u64::MAX.to_le_bytes()); // TxOut.value
// VarInt for script length = 0xffff_ffff (needs 0xff marker + u64)
buf.push(0xff);
buf.extend(0xffff_ffffu64.to_le_bytes());
// No actual script bytes follow.

let mut slice: &[u8] = &buf;
// Allocates ~4 GiB (or aborts) before discovering the payload is absent.
let _ = ReceivedOutput::read(&mut slice);
```
Each `ReceivedOutput` decoded from an untrusted stream can request an arbitrary-size allocation, so a short message reliably kills the deserializing process.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
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

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
  }
```

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
