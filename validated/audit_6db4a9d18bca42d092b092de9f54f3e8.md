### Title
Per-transaction `data`/`presumed_origin` applied to all accumulated outputs, overwriting earlier deposits' instructions - (File: `processor/src/networks/bitcoin.rs`)

### Summary
`Bitcoin::get_outputs` accumulates `ReceivedOutput`s in a single `outputs` vector declared *outside* the per-transaction loop. After scanning each transaction, the "populate origin and data" pass iterates over `&mut outputs` — the entire accumulation so far — and assigns the *current* transaction's `presumed_origin` and `data` to every output, including outputs that came from earlier transactions in the same block. A deposit from an earlier transaction therefore has its `OutputType::External` instruction data silently replaced (or erased) by a later transaction's metadata, analogous to the Compounder report where a valid item in a processed list is mishandled and the user's value is lost.

### Finding Description
In `get_outputs`, `outputs` is initialized once before iterating `block.txdata[1 ..]`. Within each iteration, newly scanned outputs are pushed, then a single `presumed_origin` (derived from `tx.input[0]`'s spent output) and a single `data` (from `extract_serai_data(tx)`) are computed *for the current `tx` only* — yet applied via `for output in &mut outputs` to every output gathered so far, including those from previous transactions:

```rust
// processor/src/networks/bitcoin.rs
let mut outputs = vec![];               // outside the tx loop
for tx in &block.txdata[1 ..] {
  for output in scanner.scan_transaction(tx) {
    ...
    outputs.push(output);               // outputs from prior txs stay in the vec
  }
  if outputs.is_empty() { continue; }
  let presumed_origin = { /* tx.input[0] of THIS tx */ };
  let data = Self::extract_serai_data(tx); // data of THIS tx
  for output in &mut outputs {          // mutates ALL outputs, incl. earlier txs
    if output.kind == OutputType::External {
      output.data.clone_from(&data);
    }
    output.presumed_origin.clone_from(&presumed_origin);
  }
}
```

Consequences:
- An earlier `External` output whose transaction carried a legitimate `InInstruction` in an OP_RETURN or a `SHA256 PUSH OP_EQUALVERIFY` witness has that `data` overwritten by the *next* matching transaction's data (or by `vec![]` if the later tx carries none).
- `presumed_origin` of every earlier output is rewritten to the later tx's first input's source address.
- If the last matching tx in the block carries empty data, all earlier `External` outputs in the block end up with empty `data`, losing their instructions entirely.

### Impact Explanation
`data` on an `External` output carries the Serai InInstruction that determines how deposited funds are processed/credited. An unprivileged attacker who observes a victim's deposit transaction to the multisig's external address in the mempool can broadcast their own small deposit to the same key; if it lands in the same block, the victim's instruction data is clobbered by the attacker's (or emptied). Funds are then "received" by the processor but bound to the wrong/absent instruction — value credited to the wrong operation or stranded without instruction, i.e., funds reported received that are not spendable/creditable as intended. `presumed_origin` corruption additionally breaks refund/origin tracking for every co-block deposit.

### Likelihood Explanation
Sending a transaction paying the multisig's well-known P2TR external address requires no privilege — any Bitcoin user can do it. The trigger condition (two transactions to the same key in one block) occurs naturally whenever multiple users deposit concurrently, and an attacker can deliberately time a transaction to coincide with a target block. No secret material, collusion, or validator status is needed.

### Recommendation
Move the `outputs` vector inside the `for tx in &block.txdata[1 ..]` loop (or track per-tx spans) so `presumed_origin` and `data` are only applied to outputs scanned from that transaction, then extend a separate result vector:

```rust
for tx in &block.txdata[1 ..] {
  let mut tx_outputs = vec![];
  for output in scanner.scan_transaction(tx) { ... tx_outputs.push(output); }
  if tx_outputs.is_empty() { continue; }
  // compute presumed_origin/data for THIS tx, apply to tx_outputs only
  outputs.extend(tx_outputs);
}
```

Alternatively, populate each `Output`'s `data`/`presumed_origin` at push time when the producing `tx` is still in scope.

### Proof of Concept
1. Victim broadcasts `tx_v` paying the multisig external address with an OP_RETURN InInstruction (e.g., a swap/refund instruction).
2. Attacker broadcasts `tx_a` paying the same address with no OP_RETURN data (or a different instruction).
3. Both confirm in block `B`, `tx_v` before `tx_a` in `txdata`.
4. `get_outputs(B, key)` scans `tx_v`, pushes the victim `External` output with correct data — then on the `tx_a` iteration, `output.data.clone_from(&vec![])` erases it and `presumed_origin` is overwritten with `tx_a`'s input origin.
5. The processor emits a `ScannerEvent::Block` reporting the victim's deposit with empty/foreign `data`; the victim's instruction is never executed — funds are held but not credited as intended.

The root cause is confirmed at `processor/src/networks/bitcoin.rs:689` (`outputs` declared before the loop) and `processor/src/networks/bitcoin.rs:731-736` (metadata applied to the full accumulated vector rather than only the current transaction's outputs).