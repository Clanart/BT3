The bug class here is "a funds-receiving/spending path that silently fails, leaving value stuck." Mapping onto Serai's bitcoin wallet/scanner, the matching real shape is the scanner's handling of coinbase transactions.

### Title
Coinbase outputs paying the multisig are permanently ignored by the scanner, leaving received funds unspendable - (File: processor/src/networks/bitcoin.rs)

### Summary
Analogous to `transfer()` making a withdrawal silently impossible, `Bitcoin::get_outputs` unconditionally skips `block.txdata[0]` (the coinbase). The stated reason — coinbase maturity — only lasts 100 blocks, but the output is skipped forever. Any miner paying to a Serai multisig address via a coinbase produces funds that are on-chain, owned by the threshold key, yet never reported and therefore never spendable. They are effectively burned.

### Finding Description
`Scanner::scan_block` includes the coinbase and correctly documents it is "bound by maturity" (`networks/bitcoin/src/wallet/mod.rs:216-227`). The processor-layer wrapper `get_outputs`, however, hard-skips the coinbase on every block:

```rust
// Skip the coinbase transaction which is burdened by maturity
for tx in &block.txdata[1 ..] {
``` [1](#0-0) 

There is no mechanism to revisit a matured coinbase output. A `ReceivedOutput` only exists if `scan_transaction` is invoked on the containing transaction [2](#0-1) , and `get_outputs` is the sole path feeding the processor scanner (`processor/src/multisigs/scanner.rs:562-567`). Once the block is scanned and acked, the coinbase output is permanently invisible: it is never emitted as a `ScannerEvent::Block` output, never enters the scheduler's input set, and `SignableTransaction::new`/`multisig` can never be built over it.

The spend path itself is fine — `multisig` validates `p2tr_script_buf(offset.group_key())` against the prevout script and signs a valid BIP-340 key-spend [3](#0-2)  — so the stuck-ness comes purely from the crediting side, exactly like the reported bug where ETH accounting is credited but the send can never succeed.

### Impact Explanation
An unprivileged party who mines a block (permissionless on Bitcoin; on regtest-style integrations trivial) can direct a coinbase payout — or a miner can be paid to do so — to a Serai `external_address`/`branch_address`/offset script. The value is locked under the multisig's x-only key forever: the scanner never reports it, so no `Plan` is ever scheduled and no signature is ever produced for that outpoint. This is a permanent, unrecoverable loss-of-funds primitive that also lets a miner burn arbitrary BTC "into" a Serai address to create discrepancies between observed on-chain balance and tracked spendable balance.

### Likelihood Explanation
Requires the sender to control a coinbase (mining a block or paying a miner). This is expensive relative to normal deposits, so likelihood is low — but it is permissionless, requires no collusion or key knowledge, and the failure is silent and irreversible rather than reverting. Consistent with the original M-02's medium severity: the failure mode is conditional, but when triggered the funds are permanently inaccessible.

### Recommendation
In `Bitcoin::get_outputs`, do not drop `txdata[0]` wholesale. Either:
- scan the coinbase but mark its outputs immature and defer emission until `block_number + 100 <= current` confirmations have elapsed (track maturity in the scanner DB), or
- re-scan matured coinbase outputs when processing later blocks (e.g., on each new block, check the coinbase of `block_number - COINBASE_MATURITY`).

At minimum, if coinbase outputs are intentionally unsupported, reject them explicitly and document that paying a Serai address via coinbase burns funds.

### Proof of Concept
Conceptual: a miner mines a block whose coinbase `txdata[0].output[0].script_pubkey == p2tr_script_buf(multisig_key)` (or any registered offset script). After `N::CONFIRMATIONS` blocks, `Scanner` emits no `ScannerEvent::Block` containing that output because `get_outputs` iterates `block.txdata[1 ..]` only. The output remains immature-then-mature on-chain, but no code path ever produces a `ReceivedOutput` for it, so `SignableTransaction::new` can never include it — the BTC is unspendable despite the multisig holding the key.

### Citations

**File:** processor/src/networks/bitcoin.rs (L690-692)
```rust
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
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
