### Title
Zero-value/dust outputs pollute the scanned UTXO set, making received funds unspendable and unbounded - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` registers every transaction output whose `script_pubkey` matches a registered key/offset, with no check on `output.value`. An unprivileged third party can send dust (including 0-value or sub-dust) outputs to a Serai multisig address, and each is recorded as a `ReceivedOutput` — the analog of `createOrder` accepting `amountIn = 0`. Like `pendingOrderIds`, the resulting UTXO list grows without bound and every later operation over it (scheduling, input selection, per-input offset re-keying during signing) pays linear cost on entries that can never profitably be spent.

### Finding Description
`scan_transaction` iterates `tx.output` and pushes a `ReceivedOutput` for every matching `script_pubkey`, capturing `output.value` verbatim:

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(), ... });
}
``` [1](#0-0) 

There is no minimum-value filter — no equivalent of `checkMinOrderSize` or `require(amountIn > 0)`. `ReceivedOutput::value()` just returns `output.value.to_sat()` [2](#0-1) , and `ReceivedOutput::read` performs no value validation either [3](#0-2) .

Each such output is keyed to a distinct `OutPoint` and carries the per-output `offset` scalar used to re-derive the spendable key [4](#0-3) , so every spam entry is unique and cannot be deduplicated. A single transaction can carry thousands of matching outputs in one call to `scan_transaction`/`scan_block` [5](#0-4) .

### Impact Explanation
- **Funds reported received that are not spendable:** a 0-sat or sub-fee output is returned to the processor as a received output, yet spending it costs more in fees than it recovers (a 0-value input can never be spent — it contributes nothing to fee/output values but still consumes input weight and a signature share).
- **Linear-cost DoS on subsequent processing:** the pending-output list grows for the cost of dust only. Every downstream pass over the UTXO set — sorting, `MAX_INPUTS` chunking, per-input offset application and nonce/signature work in `send.rs` — iterates all spam entries. This mirrors the Oku report exactly: removal/processing becomes progressively more expensive per legitimate operation because the list is full of entries that were free to create and useless to process.

### Likelihood Explanation
Reachable by any unprivileged party: the multisig/branch addresses are public on-chain, and anyone can craft a transaction with many dust outputs paying to the known `script_pubkey`. Cost to the attacker is only the dust value plus one transaction's fee per batch of outputs, while each output permanently enlarges the scanned set until spent or pruned — the same asymmetry as creating `0 amountIn` orders.

### Recommendation
Apply the report's mitigation pattern in `scan_transaction`: skip outputs whose `value` is below a dust/economical-spendability threshold (e.g., reject `output.value == 0` and anything under a minimum relay dust limit), so junk outputs never enter the `ReceivedOutput` set — equivalent to `MASTER.checkMinOrderSize(tokenIn, amountIn)`.

### Proof of Concept
1. Obtain the multisig's P2TR `script_pubkey` (it is public; `p2tr_script_buf(key)` [6](#0-5) ).
2. Broadcast a transaction with N outputs all paying `value = 0`/`546` sats to that script.
3. `Scanner::scan_transaction` returns N `ReceivedOutput`s; each is a distinct `OutPoint` and is appended to the pending UTXO list. Repeat to grow the list arbitrarily at near-zero cost, inflating every later scan/sort/sign pass — and any 0-value entries are permanently unspendable.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-86)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L116-118)
```rust
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
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

**File:** networks/bitcoin/src/wallet/mod.rs (L221-227)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```
