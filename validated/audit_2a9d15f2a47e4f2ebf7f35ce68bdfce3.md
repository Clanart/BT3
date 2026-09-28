### Title
`ReceivedOutput::read` accepts a script_pubkey/offset pair that isn't verified against the group key, letting attacker-supplied bytes register unspendable "received" funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The referenced bug class is a missing validation of a destination/account field at intake, producing funds that are recorded but can never move (the fee transfer fails even on cancellation). In bitcoin-serai, `ReceivedOutput::read` deserializes `(offset, output, outpoint)` purely syntactically and never checks that `output.script_pubkey == p2tr_script_buf(group_key + offset·G)`. The only place that consistency is enforced is `SignableTransaction::multisig`, which returns `None` — i.e., the mismatch is only detected when it's too late and the funds are already counted as received.

### Finding Description
`ReceivedOutput` stores a scalar `offset` plus a raw `TxOut`/`OutPoint` [1](#0-0) . Its `read` constructor validates only that the bytes decode — `Secp256k1::read_F` for the offset and consensus decode for `TxOut`/`OutPoint` — with no binding between the offset and the script_pubkey [2](#0-1) . The honest producer path (`Scanner::scan_transaction`) always derives `offset` from a `script_pubkey → offset` map keyed by `key + offset·G` [3](#0-2) , so the two fields are consistent there — but nothing in `read` re-establishes that invariant for bytes that didn't come from the local scanner.

The processor's `Output::read` consumes exactly this unchecked `ReceivedOutput` (plus an unchecked `kind`, `presumed_origin`, and arbitrary `data` blob) from a byte stream [4](#0-3) . `Output::balance()` then reports `Amount(self.output.value())` unconditionally [5](#0-4) , and `key()` reconstructs a key from the *script's* embedded x-only key minus `offset·G` rather than rejecting non-matching scripts [6](#0-5)  — so any P2TR-shaped script deserializes into a "received output" attributed to some key.

Spendability is only checked later in `SignableTransaction::multisig`, where `p2tr_script_buf(offset.group_key()) != prevouts[i].script_pubkey` causes the whole machine to return `None` [7](#0-6) . This mirrors the report exactly: the invalid destination is accepted at intake, the value is booked as owned, and recovery is impossible because the spending path is where validation finally fails.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read`/`Output::read` (an allowed reachability surface) can cause funds to be reported as received under outputs the threshold group cannot sign for — either a script_pubkey paying an attacker key, or a genuine group output paired with a wrong offset. In the first case the balance is phantom (group pays out against funds it never had); in the second, real UTXOs become permanently unspendable since `multisig` aborts — locked funds identical in effect to the Airlock bug.

### Likelihood Explanation
Any deserialization of peer/DB-supplied `Output`/`ReceivedOutput` bytes reaches the bug with no authentication of the offset↔script relation. The `Output::read` path even `unwrap()`s on decode, and `key()` only panics on non-P2TR scripts, so the unchecked case is the common case, not an edge.

### Recommendation
Make `ReceivedOutput` validate the binding at construction: take (or store) the expected group key, and in `read` (and in `Output::read`) recompute `p2tr_script_buf(key + GENERATOR * offset)` and reject when it doesn't equal `output.script_pubkey`. Alternatively, store the key on `ReceivedOutput` and have `multisig`'s existing check run inside `read` so invalid outputs can never enter the bookkeeping pipeline.

### Proof of Concept
1. Serialize a `ReceivedOutput` with `offset = Scalar::ZERO` (or an arbitrary scalar) and a `TxOut` whose `script_pubkey` is `ScriptBuf::new_p2tr_tweaked` of an attacker-chosen x-only key with any value `V`.
2. Feed it through `ReceivedOutput::read` (or wrap in `Output::read` with `kind`/`presumed_origin`/`data`). It deserializes successfully — no check ties the script to `key + offset·G` [2](#0-1) .
3. `balance()` reports `V` satoshis as received [5](#0-4) .
4. Attempt to spend it: `SignableTransaction::new` accepts it as an input, but `multisig(keys)` returns `None` at the `p2tr_script_buf(offset.group_key()) != prevouts[i].script_pubkey` check [8](#0-7)  — the reported funds can never be moved.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L90-97)
```rust
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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

**File:** processor/src/networks/bitcoin.rs (L128-130)
```rust
  fn balance(&self) -> ExternalBalance {
    ExternalBalance { coin: ExternalCoin::Bitcoin, amount: Amount(self.output.value()) }
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
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
```
