### Title
Per-block accumulation of outputs causes every scanned output to inherit the *last* paying transaction's origin and InInstruction data, letting an attacker overwrite a victim's instruction - ([File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary
The external report's bug class is a wrong conditional/aggregation choice (taking `min(poolAmountFromA, poolAmountFromB)` where one side is zero) that makes a required step silently not apply while the surrounding flow proceeds as if it succeeded. In Serai's Bitcoin processor, `Bitcoin::get_outputs` has the same shape: an accumulation/guard error causes the per-transaction `presumed_origin` and InInstruction `data` to be applied to the wrong outputs — every scanned output in the block is stamped with the data extracted from the *last* transaction that produced any output, while the function still returns all outputs as if each was correctly attributed.

### Finding Description
In `get_outputs`, `outputs` is declared once before the loop over `block.txdata[1 ..]` and is appended to for every transaction in the block, never reset per transaction. After pushing a tx's outputs, the code checks `if outputs.is_empty() { continue; }` — but once any earlier tx produced an output, this guard is never true again, so for every subsequent scanned tx the loop recomputes `presumed_origin` (via an RPC fetch of `tx.input[0]`'s spent output) and `data = Self::extract_serai_data(tx)`, then unconditionally overwrites `presumed_origin` on *all* accumulated outputs and `data` on every accumulated output whose `kind == OutputType::External` [1](#0-0) [2](#0-1) .

Concretely: a victim's deposit transaction in tx1 produces an `External` output carrying the victim's embedded `InInstruction` (parsed by `extract_serai_data` from the witness script). An attacker's transaction in tx2 — later in the same block — only needs to produce *any* output matching the scanner's registered scripts (e.g., a payment to the well-known external/branch/forward addresses derived from `scanner(key)`) to trigger the population block again. The attacker's `data` is then `clone_from`'d into the victim's `External` output, replacing the victim's instruction entirely.

Analogous to M-2: the intended check (`is_empty`) and the data-attribution step are mis-scoped, so the correct per-transaction attribution silently never happens for all but the last contributing transaction, and the function returns success with corrupted results.

### Impact Explanation
`Output::data()` feeds `InInstruction` execution on the Serai side (see `substrate/in-instructions/pallet/src/lib.rs` where instruction data drives `add_liquidity`/`Swap` with attacker-chosen coins and destination addresses). An attacker can therefore cause a victim's confirmed Bitcoin deposit to be processed under an attacker-supplied instruction — e.g., swapping to a coin and `out_address` controlled by the attacker — diverting the funds. This is "funds reported received" but credited/routed according to the wrong parameters: the deposit is real, the scanner emits it, yet the attached instruction is the attacker's. Reachability is purely public inputs: the attacker only needs to land a transaction paying to a scanned Serai script in the same block, ordered after the victim's transaction (fee/ordering influence is standard mempool behavior).

### Likelihood Explanation
Requires a victim deposit and an attacker tx in the same block with at least one output to a scanned script, plus the attacker's tx carrying the malicious InInstruction witness data. The scanner registers fixed offsets for `OutputType` kinds (Branch/Change/Forwarded/External) at `scanner(key)`, so the addresses are publicly derivable — no collusion or privileged position needed. Medium likelihood; impact is high (deposit misrouting), so overall severity is High/Medium.

### Recommendation
Reset per-transaction state: move `presumed_origin`/`data` computation and the `outputs.is_empty()` guard inside a scope that only mutates the outputs produced by the current transaction, e.g. collect `let mut tx_outputs = vec![]` inside the `for tx` loop, populate those, then `outputs.extend(tx_outputs)`. Alternatively, skip the population block when the current tx produced no outputs by tracking `outputs.len()` before/after scanning the tx.

### Proof of Concept
1. Victim broadcasts tx1 in block B: input embeds victim's `InInstruction` (e.g., `Swap { out_balance: SRI, out_address: victim_native }`), output pays ≥ DUST to the External deposit script.
2. Attacker broadcasts tx2 in block B, ordered after tx1: input embeds attacker's `InInstruction` (e.g., `Swap { out_balance: XMR, out_address: attacker_external }`), output pays ≥ DUST to any scanned script (External deposit address works).
3. `get_outputs(block B, key)` runs: tx1 pushes victim's `External` output; `presumed_origin`/`data` set from tx1 — correct so far. Loop continues to tx2, pushes attacker's output, `outputs.is_empty()` is false (accumulated), so the loop refetches origin and recomputes `data` from tx2, then `for output in &mut outputs { ... output.data.clone_from(&data) }` overwrites the victim output's data with the attacker's instruction [3](#0-2) .
4. The scanner emits the block's outputs; the victim's confirmed deposit is processed with the attacker's instruction, routing the bridged value to the attacker.

### Citations

**File:** processor/src/networks/bitcoin.rs (L689-704)
```rust
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
```

**File:** processor/src/networks/bitcoin.rs (L730-736)
```rust
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
      }
```
