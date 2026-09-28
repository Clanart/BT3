### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable funds — (`networks/bitcoin/src/wallet/mod.rs`)

### Summary
`Scanner::scan_block` iterates over **all** transactions in a block, including `txdata[0]` (the coinbase), and returns matching outputs as ordinary `ReceivedOutput`s. `ReceivedOutput` carries no maturity flag, and `SignableTransaction::new` will consume any `ReceivedOutput` it is given. A coinbase output is consensus-unspendable for 100 blocks, so any caller of `scan_block` (the library's advertised block-scanning API) receives "funds received" events for outputs that cannot actually be spent, and will construct invalid transactions spending them.

### Finding Description
The Polymarket incident is a "malicious injected data causes the wallet to act on attacker-shaped inputs" bug class. Mapped onto Serai's in-scope surface, the reachable untrusted input is a Bitcoin block/transaction fed to `Scanner`. The concrete defect:

- `scan_block` at `networks/bitcoin/src/wallet/mod.rs:221-227` loops `for tx in &block.txdata` with no skip of index 0, so a P2TR coinbase output paying to a registered key/offset is returned in the result set. [1](#0-0) 
- `scan_transaction` matches only `output.script_pubkey` against the script map and builds a fully-formed `ReceivedOutput` with a real `OutPoint`, indistinguishable from a mature output. [2](#0-1) 
- `ReceivedOutput` exposes only `offset`, `output`, `outpoint`, `value` — there is no maturity/confirmations field, so a consumer cannot distinguish the immature output. [3](#0-2) 
- `SignableTransaction::new` consumes `Vec<ReceivedOutput>` and builds the spend unconditionally; there is no maturity check anywhere in the wallet stack. [4](#0-3) 
- The test `send_and_get_output` demonstrates the failure mode directly: it mines to the scanner's key, calls `scan_block`, and `assert_eq!(outputs, scanner.scan_transaction(&block.txdata[0]))` — i.e., the coinbase output is returned as a normal received output, and the test has to mine 100 additional blocks before using it. [5](#0-4) 

Notably, the in-tree consumer (`processor/src/networks/bitcoin.rs:691`) works around this by scanning `block.txdata[1 ..]`, but the library API itself — which is the in-scope artifact and is exposed to any integrator — still returns immature outputs.

### Impact Explanation
An output reported by `scan_block` will be treated as a confirmed, spendable `ReceivedOutput`. Any downstream logic that builds a `SignableTransaction` from it produces a transaction that is consensus-invalid (BIP-30/COINBASE_MATURITY rule) and can never be broadcast; worse, balance/accounting layers crediting scanned outputs will report funds as received and available when they are not. This is the exact accepted impact class: "funds reported received that are not spendable."

### Likelihood Explanation
Any miner can coinbase-pay a Serai P2TR script (miners routinely pay arbitrary scripts; also relevant on regtest-style deployments and any pool paying out to a Serai address). The trigger is a Bitcoin transaction an unprivileged miner sends — an explicitly allowed input class. Every caller that uses `scan_block` rather than manually slicing `txdata[1 ..]` is affected; the API's doc comment only "recommends" a post-processing pass, it does not enforce it, and `scan_block` is the natural API to call.

### Recommendation
Skip the coinbase inside `scan_block` (iterate `block.txdata[1 ..]`, mirroring `Bitcoin::get_outputs` in `processor/src/networks/bitcoin.rs`), or extend `ReceivedOutput`/`scan_block` to record and enforce maturity so immature outputs cannot flow into `SignableTransaction::new`. At minimum, `scan_block` should not return outputs that are guaranteed unspendable at scan time.

### Proof of Concept
From the in-tree test `networks/bitcoin/tests/wallet.rs` (`send_and_get_output`):

```rust
// Mine one block paying to p2tr_script_buf(key) — the coinbase output
rpc.rpc_call::<Vec<String>>("generatetoaddress",
    serde_json::json!([1, Address::from_script(&p2tr_script_buf(key).unwrap(), Network::Regtest).unwrap()])).await.unwrap();

// scan_block immediately returns the coinbase output as spendable:
let block = rpc.get_block(&rpc.get_block_hash(block_number).await.unwrap()).await.unwrap();
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1);                                        // reported as received
assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0)); // it IS the coinbase
```

`outputs[0]` can then be passed straight into `SignableTransaction::new`, which accepts it and produces a transaction spending an immature coinbase — invalid on any real chain until 100 confirmations. The test itself must mine 100 blocks before the output becomes usable, demonstrating that the scanner reports funds that are not spendable at report time.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L89-118)
```rust
#[derive(Clone, PartialEq, Eq, Debug)]
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

  /// The Bitcoin output for this output.
  pub fn output(&self) -> &TxOut {
    &self.output
  }

  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

  /// The value of this output.
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
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

**File:** networks/bitcoin/src/wallet/send.rs (L150-160)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

```

**File:** networks/bitcoin/tests/wallet.rs (L54-69)
```rust
  // Mine until maturity
  rpc
    .rpc_call::<Vec<String>>(
      "generatetoaddress",
      serde_json::json!([100, Address::p2sh(Script::new(), Network::Regtest).unwrap()]),
    )
    .await
    .unwrap();

  let block = rpc.get_block(&rpc.get_block_hash(block_number).await.unwrap()).await.unwrap();

  let mut outputs = scanner.scan_block(&block);
  assert_eq!(outputs, scanner.scan_transaction(&block.txdata[0]));

  assert_eq!(outputs.len(), 1);
  assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));
```
