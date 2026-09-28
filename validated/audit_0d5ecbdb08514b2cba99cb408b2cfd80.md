### Title
DoS via panic in `Output::read` on malformed/truncated bytes — `.unwrap()` on SCALE decoding and `Address::try_from` instead of returning `io::Error` - (File: processor/src/networks/bitcoin.rs)

### Summary
The external report (JLSEC-2026-1003) describes a crash when reading an invalid embedded profile (XMP) from attacker-supplied input — i.e., malformed untrusted bytes reaching a parser that dereferences/uses invalid state instead of rejecting them cleanly. The Serai analog is `Output::read` in `processor/src/networks/bitcoin.rs`: untrusted bytes deserialized through this function trigger `panic!`s (and potential unbounded allocation) rather than `io::Error`, crashing the calling process.

### Finding Description
`OutputTrait::read` for Bitcoin's `Output` decodes `presumed_origin` as `Option<Vec<u8>>` with SCALE `Decode`, and calls `.unwrap()` on the result:

```rust
// processor/src/networks/bitcoin.rs:148-155
presumed_origin: {
  let mut io_reader = scale::IoReader(reader);
  let res = Option::<Vec<u8>>::decode(&mut io_reader)
    .unwrap()
    .map(|address| Address::try_from(address).unwrap());
  reader = io_reader.0;
  res
},
```

Two distinct crash paths exist here:

1. **`.decode(...).unwrap()`**: any malformed SCALE encoding (truncated input, invalid compact-length prefix, or a claimed vector length exceeding remaining bytes) panics instead of propagating `io::Error`, unlike every other field in the same function which uses `read_exact`/`?` (`OutputType::read`, `ReceivedOutput::read`, the `data` length prefix).
2. **`Address::try_from(address).unwrap()`**: even if decoding succeeds, arbitrary bytes decode as `Some(vec![...])`, and an address payload that fails `Address::try_from` panics.

Additionally, `Option::<Vec<u8>>::decode` will attempt to allocate whatever length the attacker-encoded compact prefix claims (up to ~4 GiB) before discovering the input is short, enabling an allocation-abort crash independent of the `unwrap`.

This contrasts with the sibling parser `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`), which correctly maps `consensus_decode` failures to `io::Error::other(...)`, showing the intended error-handling contract for `read` APIs in this codebase.

### Impact Explanation
An unprivileged party who can cause the processor (or any integrator component) to deserialize attacker-controlled bytes via `Output::read` — e.g., output records transmitted over the network or read from shared storage — can reliably crash the process. In a threshold-signing deployment this halts the scanner/signing pipeline (availability loss), matching the "crash on reading invalid data" impact class of the upstream advisory. Since `Output::read` is also used to reload outputs persisted by `save_outputs`/`outputs` (`processor/src/multisigs/scanner.rs:148-159`), corruption or injection into that byte stream turns a recoverable parse error into a hard panic on every restart.

### Likelihood Explanation
Likelihood depends on integrator plumbing, but the trigger is trivial: a handful of malformed bytes (e.g., a SCALE `Option` discriminant of `0x01` followed by a large length prefix and no payload) suffices. No cryptographic assumptions, collusion, or privileged position are needed — only control over bytes passed to the `read` API. The function signature promises `io::Result`, so callers reasonably expect errors, not aborts.

### Recommendation
Replace the panicking decode with error propagation consistent with the rest of the function:

```rust
presumed_origin: {
  let mut io_reader = scale::IoReader(reader);
  let res = Option::<Vec<u8>>::decode(&mut io_reader)
    .map_err(|e| io::Error::other(format!("invalid presumed_origin: {e}")))?
    .map(|address| {
      Address::try_from(address).map_err(|_| io::Error::other("invalid address"))
    })
    .transpose()?;
  reader = io_reader.0;
  res
},
```

Optionally bound the decoded `Vec<u8>` length to the maximum representable address size before allocation.

### Proof of Concept
```rust
use std::io;
use bitcoin_serai::bitcoin;
// Output::read is reached via <Output as OutputTrait<Bitcoin>>::read

#[test]
fn poc_output_read_panics_on_malformed_bytes() {
  // kind = OutputType discriminant byte, then a SCALE Some(vec![u32::MAX; N])
  // prefix with no payload: decode() fails OR allocates ~4 GiB then fails.
  let mut bytes = vec![0u8];          // OutputType
  bytes.push(0x01);                    // Option::Some
  bytes.extend_from_slice(&[0xFF; 4]); // compact length claiming huge vec
  // Truncated: no actual payload follows
  let r: io::Result<Output> = Output::read(&mut bytes.as_slice());
  // Expected: Err(...). Actual: panic (unwrap on Err) or OOM abort.
}
```

A second variant: provide a well-formed `Some(vec![0u8; 33])` as `presumed_origin` — decode succeeds, then `Address::try_from(...).unwrap()` panics on the invalid address payload. Both crashes are reachable purely from attacker-supplied bytes and abort the process instead of returning `io::Error`. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** processor/src/networks/bitcoin.rs (L145-166)
```rust
  fn read<R: io::Read>(mut reader: &mut R) -> io::Result<Self> {
    Ok(Output {
      kind: OutputType::read(reader)?,
      presumed_origin: {
        let mut io_reader = scale::IoReader(reader);
        let res = Option::<Vec<u8>>::decode(&mut io_reader)
          .unwrap()
          .map(|address| Address::try_from(address).unwrap());
        reader = io_reader.0;
        res
      },
      output: ReceivedOutput::read(reader)?,
      data: {
        let mut data_len = [0; 2];
        reader.read_exact(&mut data_len)?;

        let mut data = vec![0; usize::from(u16::from_le_bytes(data_len))];
        reader.read_exact(&mut data)?;
        data
      },
    })
  }
```

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

**File:** processor/src/multisigs/scanner.rs (L148-160)
```rust
  fn outputs(
    txn: &D::Transaction<'_>,
    block: &<N::Block as Block<N>>::Id,
  ) -> Option<Vec<N::Output>> {
    let bytes_vec = txn.get(Self::outputs_key(block))?;
    let mut bytes: &[u8] = bytes_vec.as_ref();

    let mut res = vec![];
    while !bytes.is_empty() {
      res.push(N::Output::read(&mut bytes).unwrap());
    }
    Some(res)
  }
```
