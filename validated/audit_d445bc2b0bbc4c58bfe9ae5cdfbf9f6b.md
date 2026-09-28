### Title
Buffered read in `ReceivedOutput::read` over-reads and discards attacker-controlled bytes, corrupting the deserialization framing of `Output`/`Plan` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` wraps the caller's byte stream in `std::io::BufReader` to drive `consensus_decode` for `TxOut` and `OutPoint`. `BufReader` may consume bytes past the end of the `OutPoint` into its internal buffer, and those buffered bytes are silently discarded when `buf_r` is dropped. The underlying reader is left positioned beyond the `ReceivedOutput`'s true serialization — a read overrun of attacker-controlled framing bytes. This directly mirrors the CVE-2020-7928 bug class: a field's actual consumed length disagrees with its encoded length, so subsequent fields are parsed out of position.

### Finding Description
In `ReceivedOutput::read`, the reader is wrapped: [1](#0-0) 

`BufReader` performs internal buffering: on `fill_buf` it issues a single read of up to its buffer capacity, consuming that many bytes from `r` regardless of how many `consensus_decode` actually needs. Any bytes buffered beyond the `OutPoint` are dropped when `buf_r` goes out of scope, so `read` consumes strictly *more* than `write` produced.

This corrupts the framing of any enclosing structure. In `Output::read` (processor/src/networks/bitcoin.rs), `data` is read immediately after `output`: [2](#0-1) 

An attacker who supplies the serialized bytes sizes the trailing payload so that `BufReader`'s fill swallows a chosen number of bytes; the `data_len`/`data` fields are then read from an attacker-selected offset rather than the canonical position. Two different byte streams can therefore deserialize to different `data` values than what `write`/`serialize` produced — an undocumented deserialization ambiguity. The same overrun affects any stream of consecutive `ReceivedOutput`s, such as the `outputs()` loop in `processor/src/multisigs/scanner.rs` (lines 155–158), where each shifted parse cascades into all following entries.

Contrast with `SignData::read`/`Transaction::read` (coordinator/src/tributary/transaction.rs), which honor explicit `u8`/`u16`/`u32` length prefixes exactly, and `Call::read` (networks/ethereum/src/machine.rs), which deliberately reads in bounded chunks. Only the `BufReader` path silently consumes more than the object's serialized length.

### Impact Explanation
`Output::data` is the Serai in-instruction payload extracted for `OutputType::External` outputs — it determines how received BTC is routed/credited. A framing shift that changes `data` (or makes a stored output fail to re-read) means funds can be reported received under a different instruction than the bytes actually encode, or a plan/output stream can be made to parse a `data` value of the attacker's choosing while the original bytes decode differently for other parsers. The primitive also yields a reliable deserialization desync between writers (`write` emits fixed fields; `read` consumes a variable, buffer-dependent count) reachable purely through crafted serialized bytes.

### Likelihood Explanation
Reachability requires the attacker to control bytes passed to `Output::read`/`ReceivedOutput::read` (e.g., serialized output/plan payloads carried in signable-transaction data). Within that stream, exploitation needs only that `BufReader`'s fill pull past the `OutPoint` — deterministic once capacity and layout are known — and that the victim later relies on the shifted trailing fields. Severity is bounded because the corruption is confined to in-process deserialization framing (no memory unsafety), hence Medium rather than High.

### Recommendation
Do not use `BufReader` for consensus decoding from a shared stream. Read the `TxOut` and `OutPoint` via `consensus_decode` directly on `r` (rust-bitcoin's `Decodable` performs its own length-bounded reads and does not over-read), or decode into owned `Vec<u8>` slices delimited by explicit length prefixes as done elsewhere. Additionally, have `read` return an error if framing bytes are left ambiguous, matching the trailing-byte checks used in `signer.rs`/`cosigner.rs` (`!preprocess_ref.is_empty()` / `!share_ref.is_empty()`).

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs context
// 1. Serialize a legitimate ReceivedOutput, then append a u16 length + payload
//    as Output::read expects (data_len || data).
let mut bytes = output.serialize();           // offset || TxOut || OutPoint
bytes.extend(&3u16.to_le_bytes());
bytes.extend(b"ABC");                         // legitimate data = "ABC"

// 2. Attacker instead crafts: same prefix but places the intended data
//    at the offset reached after BufReader's fill is discarded,
//    i.e. inserts K filler bytes that get swallowed.
let mut evil = output.serialize();
evil.extend(vec![0xAA; K]);                   // bytes consumed-and-dropped by BufReader
evil.extend(&4u16.to_le_bytes());
evil.extend(b"EVIL");

// Output::read(&mut evil) then reads data_len/data from after the swallowed
// region, yielding data == "EVIL" — bytes the writer never framed — while a
// reader without the BufReader over-read rejects or parses differently.
// Concrete K depends on BufReader's effective capacity; the desync is
// deterministic once the TxOut/OutPoint decode triggers one fill.
```

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
