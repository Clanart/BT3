### Title
`ReceivedOutput::read` accepts an unbound `offset`, decoupling the spend key from the `script_pubkey` it commits to — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs:120-134))

### Summary
The bug class in the Notional report is: an input-controlled flag causes an identifier (`buyToken`) to be overwritten, so a later post-processing step keyed on that identifier (re-wrapping stETH into wstETH) is silently skipped, and the function returns a value in the wrong units. The Serai analog lives in `ReceivedOutput::read` / `Output::read`: the `offset` scalar — which determines the key able to spend the output and which `OutputType` (External/Branch/Change/Forwarded) the processor attributes to it — is deserialized from attacker-controlled bytes with no check that it matches the key committed in `output.script_pubkey`. Downstream code assumes the invariant `script_pubkey == p2tr(key + G*offset)` which `Scanner::scan_transaction` enforces by construction, but the `read` path does not.

### Finding Description
`Scanner::scan_transaction` only produces `ReceivedOutput`s whose `offset` is drawn from `self.scripts`, a map keyed by `script_pubkey`, so by construction the serialized invariant holds: the script commits to `key + G*offset` [1](#0-0) . However `ReceivedOutput::read` parses `offset` via `Secp256k1::read_F` and the `TxOut`/`OutPoint` via consensus decode, with no re-derivation of the script or binding between them [2](#0-1) . `processor::networks::bitcoin::Output::read` wraps this and is used to deserialize outputs in the coordinator/processor pipeline [3](#0-2) .

The corrupted field is then consumed in two divergent ways, mirroring the `amountBought` unit confusion:

1. `Output::key()` computes `read_G(script_key) - G*offset`, i.e. it subtracts whatever offset the bytes claim. An attacker can pick `offset = real_offset + d` to make the output report *any* key `K - G*d` — e.g. a different session's registered group key — while the on-chain script still locks funds to the original key [4](#0-3) .
2. `get_outputs` indexes `kinds[offset_repr]` to classify the output as `External`/`Branch`/`Change`/`Forwarded`; a forged offset either panics on a missing key or mislabels the output kind (e.g. attacker-sent external funds reported as `Change`, or vice versa), changing how the scheduler treats the funds and whether `data`/`presumed_origin` get attached [5](#0-4) .
3. When the scheduler later builds a `SignableTransaction`, `multisig` checks `p2tr_script_buf(keys.offset(offsets[i]).group_key()) == prevouts[i].script_pubkey` and returns `None` on mismatch [6](#0-5)  — the misattributed output becomes unspendable through the normal signing path, exactly like the bought stETH that is never wrapped.

### Impact Explanation
An unprivileged party who can feed bytes into `ReceivedOutput::read`/`Output::read` (serialized outputs transiting the processor/coordinator DB and event pipeline) can cause: (a) outputs reported received under a group key that does not control them — funds credited to the wrong multisig/session and then permanently stuck because the `multisig` script check fails; (b) misclassification of `OutputType`, e.g. an attacker's external payment reported as internal `Change`, corrupting accounting of which funds are user deposits vs. protocol-owned — directly analogous to the wstETH/stETH denomination error in the report.

### Likelihood Explanation
Reachability requires the serialized `ReceivedOutput`/`Output` bytes to cross a trust boundary where an attacker can supply or tamper with them. These structures are explicitly designed to be `write`/`read`-serialized for storage and inter-component messaging [7](#0-6) , and `Output::read` is exercised on untrusted-length data (`data_len` up to 65535) in the same decode path [8](#0-7) . No secret or validator collusion is needed — only control of the byte stream.

### Recommendation
After decoding in `ReceivedOutput::read` (or in `Output::read`), re-derive and enforce the invariant: compute `p2tr_script_buf(script_key - G*offset)` is impossible without the base key, so instead store the base `key` alongside, or have `read` take the expected `Scanner`/base key and verify `scripts.get(&output.script_pubkey) == Some(&offset)`. At minimum, `processor`'s `get_outputs`-equivalent validation should be run on deserialized outputs: check `kinds.contains_key(offset_repr)` (avoiding the panic on `kinds[offset_repr]`) and re-verify the script matches a registered offset for the claimed key before the output is credited.

### Proof of Concept
1. Honest state: vault base key `K`, scanner registered `offset_o`, attacker previously sent an output with `script_pubkey = p2tr(K + G*o)` — a legitimate external deposit.
2. Attacker (or corrupted upstream component) supplies serialized `Output` bytes identical to the real one except `offset = o + d` for chosen nonzero `d`.
3. `Output::key()` returns `K - G*d`. If `d` is chosen so `K - G*d` equals another registered session key `K'`, the coordinator attributes the UTXO to `K'`'s multisig.
4. Scheduler includes the output in a plan for `K'`; `SignableTransaction::multisig` computes `p2tr(K' + G*(o+d)) ≠ script_pubkey` and returns `None` — the funds are counted as received under `K'` but can never be signed for; alternatively a panic in `get_outputs`-style `kinds[offset_repr]` indexing DoSes block processing if the offset repr is unregistered.

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L136-148)
```rust
  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }

  /// Serialize a ReceivedOutput to a `Vec<u8>`.
  pub fn serialize(&self) -> Vec<u8> {
    let mut res = Vec::new();
    self.write(&mut res).unwrap();
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
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

**File:** processor/src/networks/bitcoin.rs (L686-700)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
  }
```
