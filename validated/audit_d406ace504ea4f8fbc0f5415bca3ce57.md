### Title
`get_outputs` attributes every scanned output in a block to the last transaction's origin/data — incorrect association of outputs to their producing transaction - (File: processor/src/networks/bitcoin.rs)

### Summary
In `Bitcoin::get_outputs`, scanned outputs are accumulated into a single `outputs` vector across all transactions in a block. The per-transaction metadata (`presumed_origin` and the Serai `InInstruction` `data`) is then applied to **every** output accumulated so far, not just the outputs produced by the current transaction. Additionally, the `if outputs.is_empty() { continue }` guard tests the cumulative vector rather than the outputs of the current transaction, so a transaction that produces no matching outputs still triggers metadata stamping of all previously collected outputs using *its* first input and *its* embedded data. This is the Serai analog of the qla2xxx bug class: a structure (the output list / the current `tx`) is dereferenced incorrectly — the wrong object supplies the data and the wrong objects receive it. [1](#0-0) 

### Finding Description
`get_outputs` iterates `block.txdata[1 ..]` (skipping the coinbase) and calls `scanner.scan_transaction(tx)`, pushing each `ReceivedOutput` into `outputs`. [2](#0-1) 

Two defects follow:

1. `outputs` is declared outside the per-transaction loop and is never segmented per transaction. The check `if outputs.is_empty() { continue }` is intended to skip transactions that yielded no scanned outputs, but it only fails when *zero* outputs have been found in the entire block so far. A transaction that pays nothing to Serai, appearing after one that does, does not `continue` — it proceeds to the stamping block. [3](#0-2) 

2. The stamping block reads `tx.input[0]` from the *current* transaction to build `presumed_origin`, extracts `data = Self::extract_serai_data(tx)` from the *current* transaction, and then writes both into `for output in &mut outputs` — mutating outputs created by earlier, unrelated transactions. Earlier outputs have their `presumed_origin` overwritten and, for `OutputType::External` outputs, their `data` field overwritten with the later transaction's `InInstruction` payload (or cleared to `vec![]` if the later transaction carries none). [4](#0-3) 

The net effect: the reported `Output` for an earlier transaction is described as originating from, and carrying the Serai instructions of, whatever transaction happens to be processed last in the block that reaches the stamping code.

### Impact Explanation
`get_outputs` is the network interface through which the processor learns about deposits/outputs received on Bitcoin, including `kind` (External/Branch/Change/Forwarded via `kinds`), `presumed_origin`, and `data` carrying the `InInstruction` (which identifies the destination chain/address for a deposit). An external, unprivileged party controls transaction ordering within a block only insofar as they can get their own transaction confirmed in the same block — which any Bitcoin user can attempt, and which routinely happens by chance in normal operation.

Consequences:
- An attacker's transaction mined after a victim's deposit in the same block causes the victim's `External` output to be reported with the attacker's `presumed_origin` and, critically, the attacker's (or empty) `data`. A deposit's `InInstruction` metadata is replaced, mis-crediting or corrupting the deposit's destination/metadata as seen by the processor.
- A subsequent transaction carrying no Serai data wipes `data` for all earlier `External` outputs (`output.data.clone_from(&data)` with `data == vec![]`), since the `kind == OutputType::External` branch still runs.

This is "funds reported received" with corrupted attribution/instruction metadata derived from public, attacker-influenced transaction data — reachable purely through Bitcoin transactions an unprivileged party sends.

### Likelihood Explanation
Any Bitcoin user can craft a transaction that lands in the same block as someone else's deposit — either opportunistically (normal block congestion makes co-occurrence common) or deliberately by watching the mempool for transactions paying Serai's P2TR script and broadcasting their own transaction (even a non-paying one suffices to trigger the stamping path) to be mined afterward. No validator, peer, or collusion assumptions are needed; the trigger is ordinary transaction data the node is expected to process. Severity is Medium: it corrupts the metadata of received outputs rather than directly forging signatures or stealing key shares, but it is fully attacker-reachable and silently misattributes deposits.

### Recommendation
Scope the stamping to the current transaction's outputs:

```rust
for tx in &block.txdata[1 ..] {
    let start = outputs.len();
    for output in scanner.scan_transaction(tx) { /* push */ }
    if outputs.len() == start { continue; }
    // compute presumed_origin / data
    for output in &mut outputs[start ..] { /* stamp */ }
}
```

Also note `tx.input[0]` will panic for a non-coinbase transaction with zero inputs only in malformed cases, but more importantly `tx.input[0]` is merely a heuristic for origin; at minimum it should only ever be associated with outputs produced by *that* `tx`.

### Proof of Concept
Conceptual trace (block with two transactions):

1. Block contains `txA` (a legitimate depositor paying the Serai P2TR key, `vout 0`, carrying `InInstruction` data `D_A`) followed by `txB` (attacker transaction — paying Serai's branch/change script, or even paying nothing to Serai at all).
2. Loop iteration for `txA`: `scan_transaction` returns 1 output → `outputs.len() == 1` → `presumed_origin = input[0]` of `txA`, `data = D_A` → output gets correct metadata.
3. Loop iteration for `txB`: suppose it produces no matching outputs (attacker pays someone else). `outputs.is_empty()` is `false` (the vec still holds `txA`'s output) → the code does *not* `continue` → `presumed_origin` is computed from `txB.input[0]`, `data` from `txB` (empty or attacker-chosen `D_B`) → `txA`'s output is overwritten: `presumed_origin` becomes the attacker's input and `data` becomes `D_B`/`vec![]` if its `kind == External`.
4. The processor receives an `Output` for the victim's deposit labeled with the attacker's origin and the attacker's (or no) InInstruction — the wrong transaction was dereferenced to describe the output.

Root cause is at `processor/src/networks/bitcoin.rs` lines 702–736: the emptiness check and the `for output in &mut outputs` mutation both operate on the block-wide accumulator rather than the current transaction's outputs. [5](#0-4)

### Citations

**File:** processor/src/networks/bitcoin.rs (L686-737)
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

      if outputs.is_empty() {
        continue;
      }

      // populate the outputs with the origin and data
      let presumed_origin = {
        // This may identify the P2WSH output *embedding the InInstruction* as the origin, which
        // would be a bit trickier to spend that a traditional output...
        // There's no risk of the InInstruction going missing as it'd already be on-chain though
        // We *could* parse out the script *without the InInstruction prefix* and declare that the
        // origin
        // TODO
        let spent_output = {
          let input = &tx.input[0];
          let mut spent_tx = input.previous_output.txid.as_raw_hash().to_byte_array();
          spent_tx.reverse();
          let mut tx;
          while {
            tx = self.rpc.get_transaction(&spent_tx).await;
            tx.is_err()
          } {
            log::error!("couldn't get transaction from bitcoin node: {tx:?}");
            sleep(Duration::from_secs(5)).await;
          }
          tx.unwrap().output.swap_remove(usize::try_from(input.previous_output.vout).unwrap())
        };
        Address::new(spent_output.script_pubkey)
      };
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
      }
    }
```
