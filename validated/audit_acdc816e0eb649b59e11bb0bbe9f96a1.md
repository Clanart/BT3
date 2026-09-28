### Title
Scanner::scan_block reports immature coinbase outputs as spendable ReceivedOutputs - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
The referenced Chainlink issue is a failure to validate that returned data corresponds to the current round — `latestRoundData` silently returns an answer carried over from a prior round, which downstream logic treats as fresh/spendable. `Scanner::scan_block` in bitcoin-serai has the identical shape: it iterates `block.txdata` including `txdata[0]` (the coinbase transaction), and reports any matching output as a `ReceivedOutput` — a type whose own doc comment declares it "A spendable output" (`networks/bitcoin/src/wallet/mod.rs:88-97`) — even though coinbase outputs are consensus-unspendable for 100 confirmations. The returned `ReceivedOutput` carries no maturity flag or block-height field, so the consumer cannot distinguish "currently valid" data from "not yet valid" data, exactly analogous to omitting `require(answeredInRound >= roundID)`. [1](#0-0) [2](#0-1) 

### Finding Description
`scan_block` calls `scan_transaction` on every transaction in the block, including the coinbase (`networks/bitcoin/src/wallet/mod.rs:221-227`). `scan_transaction` matches purely on `output.script_pubkey` (`:199-214`), with no check whether the parent transaction is a coinbase. A miner — an unprivileged party — can direct a coinbase output to a P2TR script the scanner watches (e.g., the zero-offset `p2tr_script_buf(key)` registered in `Scanner::new`). The scanner then emits a `ReceivedOutput` whose `value()` reflects the (potentially large) block reward, and whose `outpoint()` references a TXID that cannot be spent until maturity.

The doc comment on `scan_block` acknowledges maturity is needed and suggests callers post-filter (`:217-220`), but `ReceivedOutput` itself exposes no field letting the caller perform that filtering reliably — the output does not record that it came from a coinbase, and the only distinguishing signal is whether `txdata[0]` was scanned. In production, Serai's processor already works around this by slicing `&block.txdata[1 ..]` in `get_outputs` (`processor/src/networks/bitcoin.rs:686-700`), confirming that unfiltered scanning of `txdata[0]` is treated as incorrect behavior in-tree. [3](#0-2) [4](#0-3) 

### Impact Explanation
The acceptance criterion "funds reported received that are not spendable" is met exactly. Any consumer that calls `scan_block` and feeds the results into `SignableTransaction::new` will construct a transaction spending an immature coinbase input; consensus rejects it, and in a protocol context the wallet/scheduler would have credited funds that cannot actually be moved — the direct analog of a DeFi protocol acting on a stale Chainlink price it cannot distinguish from a fresh one. If the coinbase spends to a registered offset script, it is indistinguishable from a legitimate deposit of arbitrary size.

### Likelihood Explanation
Reachable by any miner with no privileges: mining a block whose coinbase pays to the target's scan script is public-input behavior (a Bitcoin transaction/miner action they cause). The only mitigating factor is the doc comment on `scan_block` advising a post-processing pass; however, the API gives no in-band mechanism to do so, and the sibling production code explicitly skips `txdata[0]`, showing the expected-correct usage is not obvious. Severity: Medium — causes incorrect funds accounting / unsatisfiable spends, not direct theft.

### Recommendation
Either skip the coinbase transaction inside `scan_block` (match `processor/src/networks/bitcoin.rs` and iterate `block.txdata[1 ..]`), or record the parent transaction's coinbase status on `ReceivedOutput` (e.g., an `immature: bool` or the scanned block height) so callers can enforce maturity. The former is simplest and matches the documented intent that `ReceivedOutput` means "spendable".

### Proof of Concept
```rust
// On regtest (or any chain), a miner sends the coinbase reward to the scanned key:
let scanner = Scanner::new(key).unwrap();
// Mine a block paying p2tr_script_buf(key)
let block = get_block_at_height(h);
let outputs = scanner.scan_block(&block);
// outputs[0] is the coinbase output: value = block reward,
// outpoint = (block.txdata[0].compute_txid(), vout)
// SignableTransaction::new(vec![outputs[0]], ...) builds a TX spending an
// immature coinbase input — consensus-invalid for 100 confirmations.
```
The in-repo test `send_and_get_output` demonstrates exactly this: it generates a block paying `p2tr_script_buf(key)` and asserts `scan_block` returns `outputs.len() == 1` at `OutPoint::new(block.txdata[0].compute_txid(), 0)` — the coinbase output — only masking the bug because it then mines 100 more blocks before spending (`networks/bitcoin/tests/wallet.rs:40-78`). [5](#0-4)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
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

**File:** networks/bitcoin/src/wallet/mod.rs (L216-227)
```rust
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
