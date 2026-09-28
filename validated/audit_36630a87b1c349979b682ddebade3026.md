### Title
`get_outputs` stamps every accumulated output with the last transaction's origin and InInstruction data — ([File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary
The analog to "reserves underreported because one component is omitted/misattributed" is that `Bitcoin::get_outputs` misattributes the metadata of received outputs: the `presumed_origin` and Serai `data` (InInstruction memo) extracted from one transaction are applied to *all* outputs accumulated so far in the block, including outputs from earlier, unrelated transactions.

### Finding Description
In `get_outputs`, the `outputs` vector is declared outside the per-transaction loop at `processor/src/networks/bitcoin.rs:689`. Inside the loop, the guard `if outputs.is_empty() { continue }` (line 702) only checks whether *any* output has been collected so far in the block — not whether the *current* `tx` produced any outputs. Consequently, the origin/data extraction block at lines 707–736 runs:

1. For every transaction *after* the first matching transaction, even transactions that sent nothing to Serai.
2. Over **all** outputs accumulated from earlier transactions, since `for output in &mut outputs` iterates the shared vector, not just the current tx's outputs.

For each such tx, `presumed_origin` is rebuilt from `tx.input[0]`'s spent output (lines 714–728) and `data` is rebuilt from `Self::extract_serai_data(tx)` (line 730), then `clone_from` overwrites the fields on every accumulated output (lines 731–735). The final reported value for every output in the block is therefore whatever the *last* processed transaction carried.

### Impact Explanation
An unprivileged Bitcoin user can get a transaction included in the same block after a victim's deposit to a Serai multisig address. Their transaction — which need not pay Serai at all — causes `get_outputs` to overwrite the victim output's `data` (the InInstruction routing memo) and `presumed_origin` with the attacker's values (most likely empty `data` and the attacker's address). Deposits are then reported with wrong/blank routing metadata and an incorrect refund origin, so funds credited via `Output::balance()` (line 128) are misrouted or unprocessable — the accounting of "what was received and from whom" is wrong, mirroring the Bunni report's understated reserves.

### Likelihood Explanation
Blocks containing multiple transactions paying Serai, or any unrelated transaction ordered after a Serai deposit, are common. The attacker only needs their transaction mined in the same block — no special positioning is guaranteed but requiring *any* later tx in the block suffices, which occurs naturally. Exploitation deterministically corrupts metadata for every earlier output in that block.

### Recommendation
Move the `is_empty` check to a per-transaction basis (e.g., collect this tx's outputs into a temporary vector, skip if empty, then stamp only those outputs) so `presumed_origin` and `data` are populated solely from the transaction that actually created the outputs.

### Proof of Concept
1. Victim broadcasts tx A paying the multisig's external (offset `Scalar::ZERO`) address with an InInstruction memo.
2. Attacker broadcasts tx B, a normal Bitcoin payment unrelated to Serai, and both are mined in the same block with B ordered after A.
3. `get_outputs` processes tx A, pushing the victim's `Output`. On tx B's iteration, `outputs.is_empty()` is false, so lines 714–735 execute against `tx.input[0]` of B and `extract_serai_data(B)`, overwriting the victim output's `presumed_origin` and clearing its `data` via `clone_from`.
4. The scanner emits the output with the attacker's origin and empty memo; downstream `balance()` accounting credits funds whose routing instruction was destroyed. [1](#0-0)

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
