### Title
Coinbase outputs reported as received and spendable without maturity validation — (`networks/bitcoin/src/wallet/mod.rs`)

### Summary

`Scanner::scan_block` scans `block.txdata` including `txdata[0]`, the coinbase transaction, and returns any matching outputs as ordinary `ReceivedOutput`s. Coinbase outputs are unspendable for 100 blocks (Bitcoin's maturity rule). The scan validates that an output's `script_pubkey` matches a registered script — the analog of checking `balanceOf(msg.sender) >= totalAmount` — but never validates the per-output precondition that the output is actually spendable — the analog of the missing `allowance >= totalAmount` check. The result mirrors the original bug class: a batch of "transfers" (spend plans) built on these outputs partially fails, because any plan consuming an immature coinbase output produces a transaction the Bitcoin network rejects.

### Finding Description

`scan_transaction` iterates every output of every transaction and emits a `ReceivedOutput` whenever `output.script_pubkey` is in `self.scripts`:

```rust
// networks/bitcoin/src/wallet/mod.rs
for (vout, output) in tx.output.iter().enumerate() {
  if let Some(offset) = self.scripts.get(&output.script_pubkey) {
    res.push(ReceivedOutput { offset: *offset, output: output.clone(),
      outpoint: OutPoint::new(tx.compute_txid(), vout) });
  }
}
``` [1](#0-0) 

`scan_block` then calls this on `block.txdata` wholesale, coinbase included, and only documents (in a comment) that "a post-processing pass is needed" if outputs must be immediately spendable: [2](#0-1) 

Downstream, `ReceivedOutput`s are treated as spendable inputs: `SignableTransaction::new` sums `input.output.value` as `input_sat` and only checks aggregate solvency (`input_sat < payment_sat + needed_fee`), with no maturity or spendability check per input: [3](#0-2) [4](#0-3) 

`SignableTransaction::multisig` verifies each prevout's `script_pubkey` corresponds to the offset key — proving the wallet will happily build and sign a spend of an immature coinbase output: [5](#0-4) 

A grep over `processor/src` finds no `coinbase`/`is_coinbase` filtering of scanned outputs (the scanner emits every output whose balance exceeds `N::DUST`), so the immature output is persisted and scheduled like any other.

### Impact Explanation

An unprivileged miner can direct a coinbase payout to Serai's P2TR script (or a registered offset script). Serai reports funds as received that are not spendable for 100 blocks. Worse, once the immature output enters the scheduler's input set, `SignableTransaction::new` may select it alongside mature inputs; the resulting fully-signed transaction is consensus-invalid (`bad-txns-premature-spend-of-coinbase`) and every payment in that transaction fails — exactly the "failed transfers alongside successful transfers due to the lack of transfer validation" pattern. Because the scheduler treats the output as available balance, retries keep selecting it, giving the miner a sustained griefing vector against payout batches for the entire maturity window at the cost of sacrificing a coinbase reward. This is analogous to a `multiTransfer` where a mid-batch `transferFrom` reverts after earlier transfers succeeded.

### Likelihood Explanation

Requires a miner (or pool operator) to deliberately pay a coinbase output to Serai's script — costly but fully within reach of an unprivileged party sending a public Bitcoin transaction. No validator collusion, leaked keys, or trusted-component misbehavior is needed. The code path (`scan_block` → output emission → plan construction) contains no compensating check in the in-scope crate; whether out-of-scope processor code discards coinbase outputs could not be confirmed, and the in-scope API explicitly places that burden on callers.

### Recommendation

In `Scanner::scan_block`, skip `block.txdata[0]` or explicitly check `tx.is_coinbase()` and exclude/maturity-tag its outputs, rather than relying on a documented post-processing pass. If immature coinbase outputs must be tracked, carry maturity metadata on `ReceivedOutput` (e.g., the block height at which it becomes spendable) and have `SignableTransaction::new`/`multisig` reject inputs that are not yet spendable — i.e., validate the per-input precondition alongside the aggregate balance check, the direct analog of validating `allowance` alongside `totalAmount`.

### Proof of Concept

1. Register/have a Serai multisig key `key` with `Scanner::new(key)` (`networks/bitcoin/src/wallet/mod.rs:162`).
2. Mine a block whose coinbase (`txdata[0]`) pays `vout 0` to `p2tr_script_buf(key)` — a standard miner's coinbase construction, no Serai cooperation required.
3. Call `scanner.scan_block(&block)` → returns a `ReceivedOutput` for `(coinbase_txid, 0)` indistinguishable from a normal payment (`networks/bitcoin/src/wallet/mod.rs:221-227`). The test suite itself demonstrates a coinbase output being scanned and returned via `send_and_get_output` (`networks/bitcoin/tests/wallet.rs:40-78`), which mines `generatetoaddress` to the key's P2TR script and then asserts `outputs.len() == 1` — the coinbase payment to the scanned script is returned as a normal `ReceivedOutput`.
4. Construct `SignableTransaction::new(vec![coinbase_output], &payments, change, None, FEE)` — it succeeds since only aggregate value/dust/fee are checked (`networks/bitcoin/src/wallet/send.rs:150-256`), `multisig` passes since the script matches the offset key, and FROST signing produces a complete transaction (`TransactionSignatureMachine::complete`, `send.rs:413-428`).
5. Broadcast: Bitcoin rejects it with premature coinbase spend; every payment in the batch fails — partial-failure analogous to the reported `multiTransfer` bug — while Serai's books record the output as received balance.

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
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
