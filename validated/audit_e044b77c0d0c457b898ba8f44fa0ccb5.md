### Title
`get_outputs` misattributes `presumed_origin` and Serai instruction `data` across transactions in the same block because the `outputs` accumulator is never cleared - (networks/bitcoin/src/processor-side scanning: `processor/src/networks/bitcoin.rs:686-739`)

### Summary

Analogous to the afEth bug — where tokens moved into a pending-withdrawal state remained counted in the denominator used for pricing — `Bitcoin::get_outputs` accumulates scanned `Output`s in a vector declared outside the per-transaction loop and never resets it. Every subsequent transaction in the block then overwrites `presumed_origin` (and for `OutputType::External`, the Serai `data`/instruction) of outputs belonging to earlier, unrelated transactions. The accounting of "which origin/data belongs to which deposit" is stale: outputs already collected are treated as belonging to the transaction currently being processed.

### Finding Description

In `get_outputs`, `outputs` is initialized once before iterating `block.txdata[1 ..]`:

```rust
// processor/src/networks/bitcoin.rs:689-700
let mut outputs = vec![];
for tx in &block.txdata[1 ..] {
  for output in scanner.scan_transaction(tx) {
    ...
    outputs.push(output);
  }

  if outputs.is_empty() {
    continue;
  }
```

Because `outputs` is not scoped to the iteration:

1. The `outputs.is_empty()` check is wrong for any transaction after the first one that produced an output. A transaction with no multisig outputs still passes the check, so `tx.input[0]` of an unrelated transaction is used to compute `presumed_origin` (lines 714-728), and `Self::extract_serai_data(tx)` is applied to previously scanned outputs (lines 730-735).
2. Any later transaction in the same block that *does* contain a multisig output re-populates `presumed_origin` and `data` on all earlier outputs with the later transaction's input-0 origin and embedded `RefundableInInstruction`/`Shorthand` data:

```rust
// processor/src/networks/bitcoin.rs:730-736
let data = Self::extract_serai_data(tx);
for output in &mut outputs {
  if output.kind == OutputType::External {
    output.data.clone_from(&data);
  }
  output.presumed_origin.clone_from(&presumed_origin);
}
```

`presumed_origin` and `data` are consumed downstream by the multisig scheduler (refund plans use the origin address; `OutputType::External` data carries the Serai `InInstruction` determining the mint/burn action for the deposit). So an output's associated instruction and refund address can be silently replaced by those of a different transaction mined in the same block.

### Impact Explanation

Anyone who can get a Bitcoin transaction into the same block as a legitimate deposit can corrupt that deposit's metadata: the victim output is reported with an attacker's `presumed_origin` (used for refunds) and/or an attacker's `data` (the Serai instruction defining how the deposit is processed). This can redirect minted funds or refunds to the attacker — funds are reported received under metadata the depositor never authorized. It is a loss-of-funds / incorrect-attribution vulnerability reachable purely with public transaction data.

### Likelihood Explanation

Two transactions in one block paying to the same scanned multisig key is common (multiple depositors per block is the normal case), so the overwrite path triggers in ordinary operation — every block with ≥2 relevant transactions mislabels the earlier one. An attacker can also deliberately wedge a transaction into the same block. No privileged position, collusion, or malformed encodings required; only standard Bitcoin transactions the unprivileged party causes to be mined.

### Recommendation

Scope the accumulator per transaction: declare `let mut outputs = vec![]` inside the `for tx` loop (collect into a separate `block_outputs` for the return value), so `presumed_origin` and `data` are only attached to outputs actually produced by that transaction. Equivalently, populate origin/data while iterating the per-tx scan results before extending the block-level vector.

### Proof of Concept

1. Attacker observes a pending victim transaction `tx_v` paying to the Serai multisig's `External` address with `data = InInstruction_victim` (mint to victim's Serai address, refund to victim's Bitcoin address).
2. Attacker broadcasts `tx_a`, also paying to the multisig `External` address, with `data = InInstruction_attacker` (e.g., a `Shorthand::Raw(RefundableInInstruction { origin: attacker_addr, .. })` or an instruction crediting the attacker), ordered after `tx_v` in the block.
3. `get_outputs` scans `tx_v`, pushes its `Output`, then scans `tx_a`, pushes its `Output`, and executes the populate loop once (for `tx_a`), overwriting `tx_v`'s output `data` with `InInstruction_attacker` and `presumed_origin` with `tx_a.input[0]`'s address.
4. The scanner emits both outputs with attacker-controlled metadata; the scheduler processes the victim's deposited funds under the attacker's instruction, crediting/refunding value to the attacker instead of the victim. [1](#0-0)

### Citations

**File:** processor/src/networks/bitcoin.rs (L686-739)
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
```
