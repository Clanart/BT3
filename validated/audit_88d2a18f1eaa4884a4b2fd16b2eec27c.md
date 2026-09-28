I have limited iterations left; let me verify the suspicious accumulation logic in `get_outputs`.### Title
Accumulated `outputs` vector causes later transactions' origin/InInstruction data to overwrite earlier deposits in `Bitcoin::get_outputs` - (File: processor/src/networks/bitcoin.rs)

### Summary
In `Bitcoin::get_outputs`, the `outputs` vector is declared once before the per-transaction loop and is never reset per transaction. The guard `if outputs.is_empty() { continue; }` therefore becomes false for the remainder of the block after the first transaction that yields a Serai output. Every subsequent transaction — including transactions containing **no** Serai outputs at all — triggers re-computation of `presumed_origin` (from `tx.input[0]`) and `data` (from `extract_serai_data(tx)`), and the final `for output in &mut outputs` loop overwrites `presumed_origin` on *all* accumulated outputs and `data` on all accumulated `External` outputs. The result: every output in a block ends up stamped with the metadata of the *last* transaction iterated, not the transaction that actually created it.

### Finding Description [1](#0-0) 

The loop at lines 691–737 iterates `block.txdata[1 ..]`:

- `outputs` (line 689) is shared across iterations — outputs from earlier transactions remain in it.
- Line 702 only skips metadata population when *nothing* has been found yet, not when the *current* `tx` produced no outputs.
- Lines 731–736 mutate **all** outputs in the accumulated vector, assigning `data` (for `OutputType::External`) and `presumed_origin` for every output regardless of which transaction produced it.

This is the same defect class as the PREVAIL report: state that should be scoped/updated per-item is silently left stale (or wrongly overwritten) when the update path doesn't actually apply to the item being checked. Here, the per-transaction metadata update is applied to outputs belonging to *other* transactions, and an attacker-controlled transaction with no Serai output still forces the overwrite.

`extract_serai_data` parses the InInstruction embedded in the transaction (the `data` field drives which Serai instruction a deposit is credited to), and `presumed_origin` records the depositing address. Both are stored on `Output` and consumed downstream by the processor's multisig/scanner logic (`OutputTrait::data` / `presumed_origin`, lines 124–134).

### Impact Explanation
An unprivileged party who gets a transaction mined in the same block *after* a victim's deposit transaction causes the victim's `External` output to be recorded with the attacker's `presumed_origin` and, critically, the attacker's InInstruction `data` (or empty data if the attacker's tx carries none). Deposits can therefore be credited to the wrong Serai account/instruction, or a valid InInstruction can be stripped/replaced — funds are reported as received under the attacker's parameters rather than the depositor's. Additionally, `tx.input[0]` and the RPC fetch are executed for unrelated transactions, but the primary harm is metadata mis-attribution on real outputs.

### Likelihood Explanation
Exploitation requires only that the attacker's transaction appear later in the same block as a deposit to the multisig — something achievable by observing the mempool and broadcasting a transaction with a competitive fee, or simply by chance. No threshold compromise, validator collusion, or malformed cryptography is needed; the bug is pure control-flow on public block data.

### Recommendation
Scope the metadata population to the outputs produced by the current transaction. Collect `scanner.scan_transaction(tx)` results into a per-transaction vector, `continue` if that vector is empty, populate `presumed_origin`/`data` on those outputs only, then extend `outputs`. E.g., compute `let mut tx_outputs = ...` inside the loop, populate them, and `outputs.extend(tx_outputs)`.

### Proof of Concept
1. Victim broadcasts deposit tx `T1` paying the multisig's external address, embedding an InInstruction crediting the victim's Serai account.
2. Attacker observes `T1` in the mempool and broadcasts `T2` — any transaction, even one paying an unrelated address (or carrying the attacker's own InInstruction).
3. Miner orders `T2` after `T1` in the same block.
4. In `get_outputs`, `T1`'s scan pushes a `ReceivedOutput` (kind `External`). `T2`'s scan pushes nothing, but `outputs.is_empty()` is false, so `presumed_origin` is computed from `T2.input[0]` and `data` from `T2`; the loop at 731 overwrites `T1`'s output's `data`/`presumed_origin` with `T2`'s.
5. The deposit is reported with the attacker's origin/instruction data — credited incorrectly or stripped of its InInstruction.

### Citations

**File:** processor/src/networks/bitcoin.rs (L686-740)
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

    outputs
  }
```
