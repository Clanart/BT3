### Title
Cross-transaction metadata contamination causes Bitcoin deposits to execute attacker-controlled instructions - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::get_outputs` accumulates scanned outputs in a block-level `outputs` vector, but after each transaction it updates **every accumulated output**, not just the outputs produced by that transaction. A later attacker-controlled transaction in the same block can therefore overwrite the `data` and `presumed_origin` of an earlier victim deposit, causing Serai to process that deposit under an attacker-controlled `RefundableInInstruction`.

### Finding Description
In `Bitcoin::get_outputs`, `outputs` is initialized once before iterating over all non-coinbase transactions:

```rust
let mut outputs = vec![];
for tx in &block.txdata[1 ..] {
  for output in scanner.scan_transaction(tx) {
    ...
    outputs.push(output);
  }

  if outputs.is_empty() {
    continue;
  }

  let presumed_origin = ... tx.input[0] ...;
  let data = Self::extract_serai_data(tx);
  for output in &mut outputs {
    if output.kind == OutputType::External {
      output.data.clone_from(&data);
    }
    output.presumed_origin.clone_from(&presumed_origin);
  }
}
```

The defect is that the final loop iterates over `outputs`, which contains results from all prior transactions, rather than only the outputs scanned from the current `tx`.

`extract_serai_data` accepts attacker-controlled data from an `OP_RETURN` output or from a specifically formatted witness input. That metadata is later consumed by `instruction_from_output`, which decodes `output.data()` as `Shorthand`, converts it to `RefundableInInstruction`, and uses `output.presumed_origin()` as the refund/origin fallback when the instruction does not provide an explicit origin.

Therefore, if a victim transaction deposits BTC to an external Serai address in transaction `A`, and an attacker-controlled transaction `B` appears later in the same block, transaction `B` can overwrite the victim output’s metadata even if `B` does not send any funds to Serai.

### Impact Explanation
An attacker can cause BTC received by the threshold wallet to be associated with an attacker-selected Serai instruction and presumed refund origin. Depending on the instruction semantics, this can redirect the economic result of the victim’s deposit, such as minting/swapping proceeds to an attacker-selected Serai address or causing a refund to be attributed to the attacker’s Bitcoin origin.

The deposited UTXO remains cryptographically spendable by the multisig, but its bridge-level interpretation is corrupted. This makes the issue a concrete deposit-accounting integrity failure reachable entirely with public Bitcoin transactions.

### Likelihood Explanation
The attacker must get their transaction ordered after the victim’s transaction in the same confirmed block. This does not require validator compromise, malformed cryptographic encodings, RPC compromise, or control of the victim’s keys. It can be attempted by broadcasting a suitable transaction or arranging block inclusion through normal Bitcoin transaction propagation/mining.

The attacker transaction needs only:

1. A spendable input controlled by the attacker.
2. An `OP_RETURN` output carrying a valid encoded `RefundableInInstruction`, or equivalent witness-carried data accepted by `extract_serai_data`.
3. Placement after the victim transaction in a block containing a scanned Serai output.

Because `outputs.is_empty()` is the only gate, the malicious transaction does not need to produce a scanned output itself.

### Recommendation
Track metadata per transaction rather than per block. For example:

```rust
for tx in &block.txdata[1 ..] {
  let start = outputs.len();
  for output in scanner.scan_transaction(tx) {
    ...
    outputs.push(output);
  }

  if outputs.len() == start {
    continue;
  }

  let presumed_origin = ...;
  let data = Self::extract_serai_data(tx);
  for output in &mut outputs[start ..] {
    ...
  }
}
```

Alternatively, create a transaction-local `tx_outputs` vector and extend `outputs` only after assigning that transaction’s origin and data.

A regression test should place two transactions in one block:

- First transaction deposits to the external Serai address without instruction data.
- Second transaction does not pay Serai but contains attacker-controlled `OP_RETURN` data and attacker-controlled origin input.

The first deposit must retain empty data and its own transaction origin, not inherit metadata from the second transaction.

### Proof of Concept
Conceptual block layout:

```text
block.txdata:
  [0] coinbase
  [1] victim_tx:
        input:  victim UTXO owned by victim
        output: Serai external P2TR address
  [2] attacker_tx:
        input:  attacker UTXO owned by attacker
        output: OP_RETURN(attacker_refundable_instruction)
```

Execution:

1. `scanner.scan_transaction(victim_tx)` returns a `ReceivedOutput`.
2. `get_outputs` pushes it into `outputs` as `OutputType::External`.
3. For `victim_tx`, the code assigns the victim transaction’s origin and empty data.
4. `scanner.scan_transaction(attacker_tx)` returns no outputs.
5. Because `outputs` is still non-empty, the code computes `attacker_origin` and `attacker_data` from `attacker_tx`.
6. The loop over `&mut outputs` overwrites the victim output’s `presumed_origin` with `attacker_origin` and its `data` with `attacker_data`.
7. `instruction_from_output` later decodes the attacker data and processes the victim deposit as though the attacker supplied the deposit instruction.

The root cause is the missing transaction slice/index boundary at `processor/src/networks/bitcoin.rs:689-736`.