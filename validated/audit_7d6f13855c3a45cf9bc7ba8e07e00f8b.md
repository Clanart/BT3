### Title
Bitcoin outputs in one block inherit the last matching transaction’s origin and instruction data - ([File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary
`Bitcoin::get_outputs` accumulates scanned outputs across all transactions in a block, but then applies the currently processed transaction’s `presumed_origin` and Serai payload to every accumulated `External` output. A Bitcoin transaction sender can place a second matching transaction in the same block to overwrite the metadata associated with an earlier depositor’s output.

### Finding Description
`get_outputs` declares `outputs` outside the per-transaction loop at `processor/src/networks/bitcoin.rs:689` and appends every scanned output at lines 692-700. After a later transaction produces any match, the code derives `presumed_origin` from that transaction’s first input and extracts that transaction’s Serai payload. The loop at lines 731-735 then assigns that payload and origin to every output in the shared vector, including outputs found in earlier transactions.

The invariant linking each `Output` to its producing transaction is only checked immediately when the output is appended at line 698. It is not rechecked before metadata is applied to older entries in `outputs`.

### Impact Explanation
An attacker can send a transaction paying the multisig’s External or another registered Bitcoin address in the same block as a victim’s matching transaction. The attacker’s `extract_serai_data` result and first-input origin are copied onto the victim’s `External` output.

That can replace the victim’s transfer instruction or refund origin with attacker-chosen data. The processor subsequently converts `OutputType::External` outputs into instructions using `output.data()` and `output.presumed_origin()` in `processor/src/multisigs/mod.rs:43-91`, so the victim’s deposited amount can be processed under the attacker’s instruction or refunded to the attacker-derived origin.

### Likelihood Explanation
The attacker only needs to broadcast a normal Bitcoin transaction included in the same block as another matching transaction. No validator, RPC, peer, or private-key compromise is required. Bitcoin blocks routinely contain multiple transactions, and the attacker can monitor/broadcast during the same confirmation window.

### Recommendation
Keep scanned outputs grouped by transaction, and populate `presumed_origin`/`data` only for outputs produced by the current `tx`. For example, maintain a separate `tx_outputs` vector inside the `for tx in &block.txdata[1 ..]` loop, apply metadata to that vector only, and then append it to the block-level result. Also assert that every `Output` retains the transaction ID of the transaction used to annotate it.

### Proof of Concept
```rust
// In Bitcoin::get_outputs, for block.txdata = [coinbase, victim_tx, attacker_tx]:

let mut outputs = vec![];

for tx in &block.txdata[1 ..] {
  // victim_tx contributes victim_output to `outputs`.
  // attacker_tx contributes attacker_output to `outputs`.

  if outputs.is_empty() {
    continue;
  }

  // Derived solely from attacker_tx on the second iteration.
  let presumed_origin = Address::new(attacker_spent_output.script_pubkey);
  let data = Self::extract_serai_data(attacker_tx);

  for output in &mut outputs {
    // This updates BOTH attacker_output and victim_output.
    if output.kind == OutputType::External {
      output.data.clone_from(&data);
    }
    output.presumed_origin.clone_from(&presumed_origin);
  }
}
```

The resulting `victim_output` still has the victim transaction’s outpoint and value, but carries the attacker transaction’s instruction bytes and refund origin.