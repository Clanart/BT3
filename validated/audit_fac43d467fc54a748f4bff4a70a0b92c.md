### Title
Cross-transaction `data`/`presumed_origin` smuggling in Bitcoin output scanning lets an attacker bind arbitrary InInstructions to another user's deposit - ([File: processor/src/networks/bitcoin.rs])

### Summary
Analogous to HTTP request smuggling (one parser's interpretation of a message boundary differing from another's), `Bitcoin::get_outputs` interprets per-transaction metadata (`extract_serai_data(tx)` and the presumed origin from `tx.input[0]`) under a block-scope accumulator: the `outputs` vector is declared **outside** the per-transaction loop, so the "populate origin/data" pass applies the *current* transaction's extracted data and origin to *all* outputs accumulated from *earlier* transactions in the same block.

### Finding Description
In `get_outputs`, `let mut outputs = vec![];` is created before `for tx in &block.txdata[1 ..]`, and the populate block runs whenever `outputs` is non-empty — not only when the current `tx` produced outputs: [1](#0-0) 

Consequences:
1. If tx<sub>A</sub> deposits to Serai and tx<sub>B</sub> (attacker-controlled) appears later in the block, the loop body for tx<sub>B</sub> re-executes `output.data.clone_from(&data)` and `output.presumed_origin.clone_from(&presumed_origin)` over tx<sub>A</sub>'s `External` outputs, overwriting them with `Self::extract_serai_data(tx_B)` and `tx_B.input[0]`'s origin.
2. `extract_serai_data` pulls Serai InInstruction bytes embedded in the transaction (e.g., in a P2WSH-embedded instruction the comment at line 708–713 explicitly acknowledges). Thus an unprivileged attacker who gets any transaction into the same block can overwrite the instruction data attached to another user's `External` output.
3. `presumed_origin` is likewise falsified — every output in the block ends up attributed to the last-scanned tx's `input[0]` prevout address rather than its actual funder.
4. Secondary inconsistency: for a tx with multiple inputs, only `tx.input[0]` determines `presumed_origin` for all of its outputs, so origin attribution is already wrong even within a single transaction.

The scanner matches only `script_pubkey` (networks/bitcoin/src/wallet/mod.rs:199-214), so what distinguishes *whose* deposit and *what instruction* it carries is exactly the data path corrupted here. Downstream, `Output::data` is read into the coordinator's instruction handling (processor/src/multisigs/mod.rs, `Output::read` at processor/src/networks/bitcoin.rs:132-134), where the bytes are interpreted as the depositor's instructions for the received funds.

### Impact Explanation
An attacker can attach an arbitrary, self-authored `InInstruction` payload to a victim's `External` output simply by landing their own transaction in the same Bitcoin block after the victim's deposit. The coordinator will then interpret the victim's received funds under attacker-chosen instruction bytes and an attacker-chosen `presumed_origin`, i.e., the deposit's semantics are smuggled across a transaction boundary — directly mirroring the advisory's "attacker accesses a path restricted by ACL / interprets a request inconsistently" class. Depending on instruction handling, this can misroute, misattribute, or alter execution for funds the attacker does not own (e.g., origin/account the balance is credited against, or instruction behavior applied to the output's balance).

### Likelihood Explanation
Reachable by any unprivileged Bitcoin user who can get a transaction confirmed in the same block as a deposit (standard mempool behavior; no validator access needed). Exploitation requires the attacker's tx to appear after the victim's tx in `block.txdata` ordering — achievable via timing/fee bidding or as a miner — but ordering manipulation is a realistic, low-cost precondition. Requires a target deposit sharing a block, which is common in active bridge operation.

### Recommendation
Move `let mut outputs = vec![];` inside the `for tx` loop (or scope the populate pass to only the outputs produced by the current `tx`, e.g., track the index range `[start, outputs.len())`). Also reconsider deriving `presumed_origin` solely from `tx.input[0]` for multi-input transactions, or document/rename it as a heuristic. Add a regression test: two transactions in one block, the second embedding an `InInstruction`; assert the first tx's `External` output retains `data == []` and its own origin.

### Proof of Concept
Conceptual (no code execution available):

1. Honest user broadcasts deposit tx<sub>A</sub> paying `p2tr_script_buf(key)` (kind `External`, intended `data = []`).
2. Attacker broadcasts tx<sub>B</sub> that (a) spends one of the attacker's own outputs whose prevout resolves to an attacker address, and (b) embeds a malicious Serai InInstruction (e.g., via the P2WSH-in-witness embedding `extract_serai_data` parses) and lands in the same block after tx<sub>A</sub>.
3. `get_outputs` executes:
   - tx<sub>A</sub> iteration: pushes victim output, populates `data`/`presumed_origin` correctly.
   - tx<sub>B</sub> iteration: `scanner.scan_transaction(tx_B)` may return zero new outputs, but `outputs` is still non-empty, so `extract_serai_data(tx_B)` is cloned into the victim's `External` output and `presumed_origin` is overwritten with the attacker's address (lines 702–736).
4. Coordinator reads `Output` via `Output::read` (processor/src/networks/bitcoin.rs:145-165) and processes the attacker-supplied `data` as the instruction bound to the victim's funds.

Caveat: I could not fully trace the coordinator-side `InInstruction` dispatch within the tool-call budget, so the exact end-state action (crediting/misrouting) is inferred from the documented data flow; the overwrite primitive itself is directly evidenced by the cited loop structure.

### Citations

**File:** processor/src/networks/bitcoin.rs (L689-736)
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
```
