### Title
Coinbase outputs are reported as spendable before maturity - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`Scanner::scan_block` treats a coinbase output paying a watched Taproot script the same as an ordinary confirmed output, even though Bitcoin consensus prevents spending coinbase outputs for 100 blocks. The scanner checks only the output's `script_pubkey` boundary and does not check the transaction-position/maturity boundary before returning it as a `ReceivedOutput`. [1](#0-0) 

### Finding Description
`scan_transaction` returns a `ReceivedOutput` whenever `output.script_pubkey` is present in `Scanner::scripts`. [2](#0-1)  `scan_block` then iterates over all `block.txdata`, including `txdata[0]`, the coinbase transaction. [3](#0-2)  Although the function comment acknowledges maturity, `ReceivedOutput` is documented as "a spendable output," and the returned object contains only the spending offset, `TxOut`, and `OutPoint`; it carries no maturity marker or spendable-height state. [4](#0-3)  A downstream caller can pass the returned object to `SignableTransaction::new`, which uses the `ReceivedOutput`'s outpoint and value as a normal spendable input without checking whether the previous transaction was an immature coinbase. [5](#0-4) 

### Impact Explanation
A miner or any party capable of creating the coinbase transaction can pay a watched Serai address in `block.txdata[0]` and cause the wallet scanner to report funds as received when they are not yet spendable. If the consumer treats `ReceivedOutput` as immediately spendable, it can account for unavailable funds and construct a transaction that consensus will reject until maturity, delaying payments or requiring manual recovery of the pending spend. The impact is a reachable accounting/spendability violation rather than theft of private keys. [6](#0-5) 

### Likelihood Explanation
The trigger is public transaction data: the attacker only needs to include a watched Taproot script in a coinbase output and have that block accepted. No private key, validator privilege, malformed parser input, or protocol cooperation is required. The current test helper explicitly mines to the watched address, then calls `scan_block` and demonstrates equality with `scan_transaction(&block.txdata[0])`, confirming the exposed behavior. [7](#0-6) 

### Recommendation
Change `scan_block` to skip `block.txdata[0]` by default, or return a distinct immature-output type containing the block height/maturity information. If mature scanning must remain supported, add an explicit API such as `scan_block_including_coinbase` so ordinary callers cannot accidentally treat immature coinbase outputs as spendable. Add a regression test asserting that a fresh coinbase payment is not returned by the normal spendable-output API before maturity and is returned only after the applicable maturity period. [8](#0-7) 

### Proof of Concept
On regtest:

1. Create `Scanner::new(key)` for an even Taproot-compatible key. [9](#0-8) 
2. Mine exactly one block whose coinbase pays `p2tr_script_buf(key)`; do not mine the subsequent 100 blocks. [10](#0-9) 
3. Call `scanner.scan_block(&block)`. It returns one `ReceivedOutput` for `OutPoint::new(block.txdata[0].compute_txid(), 0)` because the script matches. [11](#0-10) 
4. Pass that `ReceivedOutput` to `SignableTransaction::new`. Construction succeeds because it consumes the recorded outpoint and value without a coinbase-maturity check. [5](#0-4) 
5. Broadcast the signed transaction. Bitcoin rejects the spend while the coinbase is immature, even though the scanner reported it as spendable.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-117)
```rust
/// A spendable output.
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-226)
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

  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-184)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
```

**File:** networks/bitcoin/tests/wallet.rs (L40-70)
```rust
async fn send_and_get_output(rpc: &Rpc, scanner: &Scanner, key: ProjectivePoint) -> ReceivedOutput {
  let block_number = rpc.get_latest_block_number().await.unwrap() + 1;

  rpc
    .rpc_call::<Vec<String>>(
      "generatetoaddress",
      serde_json::json!([
        1,
        Address::from_script(&p2tr_script_buf(key).unwrap(), Network::Regtest).unwrap()
      ]),
    )
    .await
    .unwrap();

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
  assert_eq!(outputs[0].value(), block.txdata[0].output[0].value.to_sat());
```
