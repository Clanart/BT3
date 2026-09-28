### Title
`ReceivedOutput::read` accepts attacker-supplied bytes without verifying the declared scalar `offset` actually derives the output's `script_pubkey`, letting a wallet treat unspendable outputs as owned — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The TLS hostname-verification bug class (accepting a credential without checking it binds to the expected identity) maps onto `ReceivedOutput`, where the `offset` field is the claim "this output's script is `p2tr(key + offset·G)` and is spendable by us". `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` from untrusted bytes with only syntactic checks — it never verifies that `output.script_pubkey == p2tr_script_buf(key + GENERATOR * offset)`. Since the scanner (the honest constructor) is the only place this invariant is established, any `ReceivedOutput` obtained via `read` (the documented untrusted-bytes path) may carry a fabricated offset, causing the wallet to report received funds it cannot actually spend.

### Finding Description
`Scanner::scan_transaction` constructs `ReceivedOutput` by looking up `output.script_pubkey` in `self.scripts`, guaranteeing `offset` matches the script [1](#0-0) . However, `ReceivedOutput::read` — reachable with untrusted bytes and explicitly listed as an attacker-controlled entry point — reads `offset` via `Secp256k1::read_F`, then `TxOut` and `OutPoint` via consensus decoding, and returns the struct without any consistency check between `offset` and `output.script_pubkey` [2](#0-1) . The struct's fields are private and `offset()` is trusted downstream as "the scalar offset to obtain the key usable to spend this output" [3](#0-2) , and consumers such as `processor/src/networks/bitcoin.rs` derive the owning key as `script_pubkey_key - offset·G` and classify the output kind purely from the offset bytes [4](#0-3) . A forged `ReceivedOutput` therefore reports `(script, offset)` pairs where the offset does not correspond to the script — the analog of accepting a certificate valid for a different host.

### Impact Explanation
Any component that ingests a serialized `ReceivedOutput` (or `Output::read`, which embeds one) from an untrusted party accepts funds as received/spendable that are not: the declared offset yields a spending key unrelated to the output's actual Taproot key, or vice versa. The wallet can be made to book deposits that the threshold key cannot sign for (producing unspendable inputs in `SignableTransaction` / failed or mis-attributed spends), a "funds reported received that are not spendable" impact. It can also misclassify the `OutputType` (`External`/`Branch`/`Change`/`Forwarded`) since that kind is keyed by the attacker-chosen offset [5](#0-4) .

### Likelihood Explanation
Exploitation requires an untrusted party to feed bytes to `ReceivedOutput::read` — a listed in-scope entry point — with a mismatched `offset`. No secret knowledge is needed; the attacker chooses any valid scalar and any TxOut. The only mitigation is contextual (whether a given deployment deserializes `ReceivedOutput` from untrusted sources rather than constructing it via `Scanner`). Within the crate's own API surface, the missing binding check is unconditional: `read` never recomputes `p2tr_script_buf(key + offset·G)` and has no access to `key`, so the check cannot happen implicitly elsewhere in `read` itself [6](#0-5) .

### Recommendation
Either (a) extend `ReceivedOutput::read` to take the scanning `key` and verify `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)`, mirroring how `Scanner` establishes the invariant, or (b) make `ReceivedOutput` non-deserializable from untrusted input and require reconstruction through `Scanner::scan_transaction`/`scan_block`, which enforces the binding. At minimum, document that `ReceivedOutput::read` performs no binding validation and every consumer must re-derive the script from `(key, offset)` before treating the output as spendable.

### Proof of Concept
Conceptual: construct bytes `offset = Scalar::ONE || TxOut{script_pubkey: p2tr_script_buf(victim_key), value: X} || outpoint` (or the inverse — a real registered script with a different registered offset's scalar). `ReceivedOutput::read(&mut bytes)` returns `Ok` [2](#0-1) ; `output.offset()` reports `ONE` while `output.output().script_pubkey` is spendable only by `victim_key` (offset `ZERO`). A consumer crediting this output and later calling `SignableTransaction::new` with it will sign under key `group_key + ONE` against a script requiring `group_key + ZERO` — the input can never be spent, yet it was reported received. No tool calls remain to fully trace whether `SignableTransaction` or the processor re-derives the script from `(key, offset)` before spending; if such a recheck exists downstream it mitigates this, but nothing in the indexed code performs it, and `processor/src/networks/bitcoin.rs` `Output::read` similarly trusts the embedded `ReceivedOutput` [7](#0-6) .

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L90-103)
```rust
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}

impl ReceivedOutput {
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
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

**File:** networks/bitcoin/src/wallet/mod.rs (L205-211)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
```

**File:** processor/src/networks/bitcoin.rs (L112-122)
```rust
  fn key(&self) -> ProjectivePoint {
    let script = &self.output.output().script_pubkey;
    assert!(script.is_p2tr());
    let Instruction::PushBytes(key) = script.instructions_minimal().last().unwrap().unwrap() else {
      panic!("last item in v1 Taproot script wasn't bytes")
    };
    let key = XOnlyPublicKey::from_slice(key.as_ref())
      .expect("last item in v1 Taproot script wasn't x-only public key");
    Secp256k1::read_G(&mut key.public_key(Parity::Even).serialize().as_slice()).unwrap() -
      (ProjectivePoint::GENERATOR * self.output.offset())
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

**File:** processor/src/networks/bitcoin.rs (L693-697)
```rust
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
```
