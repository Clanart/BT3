### Title
`get_outputs` rewrites deposit data/origin using unrelated later transactions in the same block - ([File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary

Analogous to `receiveFromDepositPool()` computing the received amount from a measurement taken over the wrong scope (a balance delta that is always zero), `Bitcoin::get_outputs` attributes `data` and `presumed_origin` to scanned outputs by iterating the *accumulated* `outputs` vector instead of the outputs produced by the current transaction. Because `outputs` is declared outside the per-transaction loop and the guard `if outputs.is_empty() { continue; }` checks the accumulator — not the current transaction's scan results — every non-coinbase transaction after the first matching one re-extracts `tx.input[0]` and `extract_serai_data(tx)` and overwrites the metadata of all previously collected outputs. An unprivileged user who gets any transaction mined in the same block after a Serai-bound deposit determines the `data` (InInstruction) attached to that deposit, or can wipe it entirely.

### Finding Description

In `processor/src/networks/bitcoin.rs`, `get_outputs` builds `outputs` once before the loop over `block.txdata[1 ..]`, pushes results of `scanner.scan_transaction(tx)` into it, and then — keyed only on `outputs.is_empty()` — assigns `presumed_origin` and `data` to *every* entry in `outputs`, for every subsequent transaction in the block [1](#0-0) . The `for output in &mut outputs` loop at the end mutates all accumulated outputs, not just those found in `tx` [2](#0-1) .

Concretely:

```rust
let mut outputs = vec![];
for tx in &block.txdata[1 ..] {
  for output in scanner.scan_transaction(tx) {
    ...
    outputs.push(output);
  }
  if outputs.is_empty() {          // checks accumulator, not this tx's results
    continue;
  }
  let presumed_origin = ... tx.input[0] ...;
  let data = Self::extract_serai_data(tx);
  for output in &mut outputs {     // overwrites ALL earlier outputs' metadata
    if output.kind == OutputType::External {
      output.data.clone_from(&data);
    }
    output.presumed_origin.clone_from(&presumed_origin);
  }
}
```

`extract_serai_data` pulls the `OP_RETURN` push (or a SegWit `SHA256 <msg> EQUALVERIFY` witness pattern) from *that* transaction [3](#0-2) . So for a block `[coinbase, deposit_tx, attacker_tx]`, `attacker_tx`'s OP_RETURN — or the empty `data` produced when it has none — replaces the `data` of the `External` output found in `deposit_tx`. `presumed_origin` is likewise overwritten with `attacker_tx.input[0]`'s spent script. The scanned `ReceivedOutput` itself (offset, value, outpoint) is correct — exactly as in the reference bug, the accounting layer attributes the wrong quantity/metadata to funds that did arrive, because the measurement was taken over the wrong boundary.

The outputs are then emitted upstream by the multisig scanner (`output.balance().amount.0 >= N::DUST` filter and `ScannerEvent::Block` emission in `processor/src/multisigs/scanner.rs`) and consumed as Serai `InInstruction` data, so corrupted `data` reaches consensus-relevant handling [4](#0-3) .

### Impact Explanation

`data` on an `OutputType::External` output is the Serai `InInstruction` (e.g., which Serai address/coins the deposit maps to). An attacker who lands a transaction in the same block as a victim's deposit — trivially achievable by broadcasting any self-payment — either (a) injects arbitrary bytes as the victim deposit's instruction, or (b) clears the instruction entirely (any tx without a matching OP_RETURN/witness pattern yields `vec![]`, which is `clone_from`'d over the real data). Mis-attributed instructions can route the credited deposit to attacker-chosen destinations or render the deposit's intent unrecoverable — funds reported received with semantics the depositor never authorized, the same "credited ≠ actually delivered" failure class as `claimableAssets` remaining 0. `presumed_origin` corruption additionally mislabels the refund/source address used for attribution.

### Likelihood Explanation

Only ordering within a block is required: the attacker's transaction merely needs to appear in the same block after any Serai-scanned transaction. No mempool manipulation of the victim tx is needed; the attacker broadcasts an ordinary transaction and can even target deposits opportunistically whenever several Serai-bound txs share a block. Every additional non-coinbase tx in the block re-triggers the overwrite, so the *last* tx in the block always wins — the bug fires on every multi-transaction block containing a Serai output, deterministic rather than probabilistic.

### Recommendation

Scope the metadata assignment to the current transaction's scan results: collect `scan_transaction` results into a per-tx vector, skip when *that* vector is empty, then push the fully-populated `Output`s into `outputs` after `presumed_origin`/`data` are applied — i.e., measure the transaction being processed, not the running total (the same fix shape as passing an explicit `amount` rather than diffing a global balance).

```rust
for tx in &block.txdata[1 ..] {
  let scanned = scanner.scan_transaction(tx);
  if scanned.is_empty() { continue; }
  let presumed_origin = ...;
  let data = Self::extract_serai_data(tx);
  for output in scanned {
    let kind = kinds[&output.offset().to_repr()[..]];
    let mut output = Output { kind, presumed_origin: presumed_origin.clone(), output, data: vec![] };
    if kind == OutputType::External { output.data = data.clone(); }
    outputs.push(output);
  }
}
```

### Proof of Concept

Conceptual, against a regtest block:

1. Victim broadcasts `deposit_tx` paying the Serai multisig's P2TR script (offset `Scalar::ZERO`, kind `External`) with an OP_RETURN encoding `InInstruction` `D_v`.
2. Attacker broadcasts `attacker_tx` — any ordinary tx, e.g., paying themselves — with an OP_RETURN `D_a`, ordered after `deposit_tx` in block `B`.
3. `get_outputs(B, key)`: `scan_transaction(deposit_tx)` yields the deposit `Output` (`data` initially `D_v`); `scan_transaction(attacker_tx)` yields nothing, but `outputs.is_empty()` is false, so `data = extract_serai_data(attacker_tx) = D_a` is `clone_from`'d into the deposit output.
4. The scanner emits the deposit with `data() == D_a` (or `[]` if `attacker_tx` had no OP_RETURN), proving per-transaction metadata was attributed from an unrelated transaction supplied entirely by the attacker's public input.

### Citations

**File:** processor/src/networks/bitcoin.rs (L493-524)
```rust
  fn extract_serai_data(tx: &Transaction) -> Vec<u8> {
    // check outputs
    let mut data = (|| {
      for output in &tx.output {
        if output.script_pubkey.is_op_return() {
          match output.script_pubkey.instructions_minimal().last() {
            Some(Ok(Instruction::PushBytes(data))) => return data.as_bytes().to_vec(),
            _ => continue,
          }
        }
      }
      vec![]
    })();

    // check inputs
    if data.is_empty() {
      for input in &tx.input {
        let witness = input.witness.to_vec();
        // expected witness at least has to have 2 items, msg and the redeem script.
        if witness.len() >= 2 {
          let redeem_script = ScriptBuf::from_bytes(witness.last().unwrap().clone());
          if Self::segwit_data_pattern(&redeem_script) == Some(true) {
            data.clone_from(&witness[witness.len() - 2]); // len() - 1 is the redeem_script
            break;
          }
        }
      }
    }

    data.truncate(MAX_DATA_LEN.try_into().unwrap());
    data
  }
```

**File:** processor/src/networks/bitcoin.rs (L689-700)
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
```

**File:** processor/src/networks/bitcoin.rs (L730-737)
```rust
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
      }
    }
```

**File:** processor/src/multisigs/scanner.rs (L562-566)
```rust
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
```
