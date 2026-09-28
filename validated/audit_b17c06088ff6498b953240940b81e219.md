### Title
Cross-transaction `data`/`presumed_origin` overwrite lets an attacker hijack any Serai-bound output's InInstruction in the same block - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::get_outputs` accumulates scanned `Output`s in a block-level `outputs` vector, then applies the *current* transaction's `extract_serai_data` and `presumed_origin` to **every** entry in that vector — including outputs produced by earlier, unrelated transactions in the same block. An unprivileged party who lands any transaction in the same Bitcoin block as a victim's Serai-bound deposit can overwrite the victim output's `data` (the encoded `Shorthand`/`RefundableInInstruction`) and `presumed_origin`, gaining unauthorized insert/update of another user's accessible data and execution of an attacker-chosen InInstruction against the victim's balance.

### Finding Description
The bug-class analog is improper isolation between attacker-supplied input and stored data belonging to other users (unauthorized update/insert of accessible data).

In `get_outputs`, `outputs` is declared once before the per-transaction loop and is never reset:

```rust
// processor/src/networks/bitcoin.rs:689-737
let mut outputs = vec![];
for tx in &block.txdata[1 ..] {
  for output in scanner.scan_transaction(tx) {
    ...
    outputs.push(output);
  }

  if outputs.is_empty() {
    continue;
  }

  // populate the outputs with the origin and data
  let presumed_origin = { /* tx.input[0]'s spent output address */ };
  let data = Self::extract_serai_data(tx);
  for output in &mut outputs {
    if output.kind == OutputType::External {
      output.data.clone_from(&data);
    }
    output.presumed_origin.clone_from(&presumed_origin);
  }
}
```

There are two distinct flaws:

1. `for output in &mut outputs` iterates the cumulative block-level vector, not just the outputs scanned from `tx`. Any transaction processed *after* a Serai-paying transaction overwrites the `data` and `presumed_origin` fields of all previously collected `External` outputs in that block.

2. The `if outputs.is_empty() { continue }` guard is also broken by the accumulation: it is meant to skip transactions which produced no Serai outputs, but once any earlier transaction pushed an output, *every* subsequent transaction — even one paying nothing to Serai — enters the populate block and rewrites the earlier outputs' fields.

The overwritten fields are security-critical downstream:

- `Output::data` feeds `instruction_from_output` (`processor/src/multisigs/mod.rs:55-91`), which decodes it as `Shorthand` → `RefundableInInstruction` and pairs it with `output.balance()` into an `InInstructionWithBalance` that mints/executes on Serai.
- `Output::presumed_origin` is used when `instruction.origin` is `None` (`instruction.origin.or(presumed_origin)`, multisigs/mod.rs:90), and `origin` is the refund destination ("If the instruction fails, coins are scheduled to be returned to `origin`", spec/integrations/Instructions.md).

### Impact Explanation
An attacker monitors the mempool for a transaction paying to a Serai External address (the P2TR script of the current multisig key, publicly knowable). They broadcast their own transaction — it need not pay Serai at all — so that it is mined in the same block after the victim's transaction. When `get_outputs` scans that block:

- The victim `External` output's `data` is replaced with the attacker's `extract_serai_data` payload, e.g. an OP_RETURN containing `Shorthand::transfer(None, attacker_serai_addr)`. Serai then executes a `Transfer` of the **victim's** deposited balance to the attacker's Serai address — theft of the minted funds.
- Alternatively/absent valid data, `presumed_origin` is replaced with the address of the attacker's first input, so a failed-instruction refund of the victim's coins is scheduled to an attacker-controlled Bitcoin address.

This is unauthorized update/insert of accessible data plus unauthorized execution of instructions on another user's funds — a direct analog of the Helidon unauthorized-write/read flaw, reachable with purely public network inputs (a Bitcoin transaction the attacker sends).

### Likelihood Explanation
Entirely unprivileged: the attacker only needs to broadcast a valid Bitcoin transaction in the same block as the victim deposit, which is trivially achievable by mempool observation and standard fee bidding. No validator status, no threshold collusion, no malformed encodings are required. Ordering dependence reduces reliability somewhat (the victim tx must precede the attacker tx in the block, and the attacker tx must not be the coinbase), but miners' ordering and mempool visibility make this a practical attack.

### Recommendation
Scope the populate loop to the outputs produced by the current transaction:

- Collect per-transaction results into a fresh `Vec`, e.g. `let mut tx_outputs = scanner.scan_transaction(tx).map(...)`, `continue` when `tx_outputs.is_empty()`, apply `presumed_origin`/`data` only to `tx_outputs`, then `outputs.extend(tx_outputs)`.
- Guard `tx.input[0]` / `previous_output.vout` indexing against malformed/coinbase-like transactions when computing `presumed_origin`.

### Proof of Concept
1. Victim broadcasts `tx_v` paying 1 BTC to the Serai External P2TR script, with an OP_RETURN encoding `Shorthand::transfer(None, victim_serai_addr)` (or no data at all).
2. Attacker broadcasts `tx_a` spending any of their own UTXOs to themselves, attaching OP_RETURN data encoding `Shorthand::transfer(None, attacker_serai_addr)`.
3. Both are mined in one block with `tx_a` after `tx_v` (neither is the coinbase, so `txdata[1 ..]` iterates both).
4. `get_outputs` scans `tx_v`, pushing the victim's `Output { kind: External, data: vec![], presumed_origin: None }`; the populate loop initially sets `data`/`presumed_origin` from `tx_v`.
5. `tx_a` is then scanned: `scan_transaction` returns nothing for it, but `outputs.is_empty()` is false (the victim output is still in the vector), so `presumed_origin` is computed from `tx_a.input[0]` and `data` from `tx_a`'s OP_RETURN, and `for output in &mut outputs` writes both onto the **victim's** output.
6. `instruction_from_output` decodes the attacker's `attacker_serai_addr` transfer instruction and executes it against `victim balance` — or, if the attacker supplied no/invalid data, the refund `origin` becomes the attacker's address.