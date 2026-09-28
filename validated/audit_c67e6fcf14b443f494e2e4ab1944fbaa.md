### Title
Bitcoin scanner leaks transaction instructions across unrelated transactions in a block - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::get_outputs` retains scanned outputs in a block-level `outputs` vector while iterating transactions, then applies the current transaction’s `presumed_origin` and Serai instruction data to every previously accumulated output, including outputs created by earlier transactions. [1](#0-0) 

### Finding Description
The function initializes `outputs` before the transaction loop, pushes all matching transaction outputs into that shared vector, and checks only whether the accumulated vector is empty—not whether the current transaction produced any outputs. [2](#0-1)  Once any Serai output has been found, every later transaction in the block causes `extract_serai_data(tx)` and the later transaction’s first-input origin to be written to all accumulated outputs. [3](#0-2)  This directly mirrors the redirect bug class: authentication/context attached to one origin is carried into a request belonging to another origin.

For external deposits, `instruction_from_output` treats `output.data()` as the deposit instruction and uses `output.balance()` as its balance. [4](#0-3)  The resulting instruction is inserted into a batch and dispatched to Substrate. [5](#0-4)  Therefore, an attacker can place a transaction after a victim’s deposit in the same block and overwrite the victim output’s instruction data and presumed origin with attacker-controlled transaction metadata.

### Impact Explanation
A victim’s Bitcoin deposit can be processed using an attacker-supplied `InInstruction`, causing the funds represented by the victim’s external output to be credited or transferred according to the attacker’s instruction rather than the victim’s intended instruction. [6](#0-5)  The overwrite also changes `presumed_origin`, which may redirect refunds to an attacker-controlled external address when no explicit instruction origin is present. [7](#0-6) 

### Likelihood Explanation
The attacker only needs to publish an ordinary Bitcoin transaction in the same confirmed block after a transaction paying a Serai external address. The later transaction does not need to pay Serai or produce a matching output; because `outputs` remains non-empty, merely iterating over it triggers the enrichment path. [8](#0-7)  Obtaining a position after the victim transaction requires miner or block-template influence, but the exploit does not require validator privileges, private keys, malformed encodings, or control over the victim.

### Recommendation
Process scanned outputs strictly per transaction: keep the enrichment loop inside a scope containing only the outputs returned by `scanner.scan_transaction(tx)`, or skip metadata extraction when that transaction produced no outputs. At minimum, the code should use a transaction-local `tx_outputs` vector and append those outputs to the block-level result only after assigning that transaction’s own `data` and `presumed_origin`.

### Proof of Concept
A minimal source-level scenario is:

1. A victim transaction `tx_victim` in a block creates a P2TR output matching Serai’s external address and contains no Serai instruction data.
2. `scanner.scan_transaction(tx_victim)` adds that output to the block-level `outputs` vector. [9](#0-8) 
3. An attacker transaction `tx_attacker` later in the same block produces no Serai outputs, but embeds attacker-controlled Serai data and spends an attacker-controlled first input.
4. Since `outputs` is still non-empty, the code does not `continue`, extracts metadata from `tx_attacker`, and applies it to the victim’s external output. [10](#0-9) 
5. `instruction_from_output` then combines the victim’s output balance with the attacker’s decoded instruction. [4](#0-3) 

The vulnerable pattern is equivalent to:

```rust
let mut outputs = vec![];

for tx in &block.txdata[1..] {
  for output in scanner.scan_transaction(tx) {
    outputs.push(Output { /* ... */ });
  }

  // This checks all prior transactions' outputs, not outputs from `tx`.
  if outputs.is_empty() {
    continue;
  }

  let data = Self::extract_serai_data(tx);
  for output in &mut outputs {
    // Overwrites metadata for outputs belonging to earlier transactions.
    output.data.clone_from(&data);
  }
}
```

### Citations

**File:** processor/src/networks/bitcoin.rs (L686-736)
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
```

**File:** processor/src/multisigs/mod.rs (L45-92)
```rust
  let presumed_origin = output.presumed_origin().map(|address| {
    ExternalAddress::new(
      address
        .try_into()
        .map_err(|_| ())
        .expect("presumed origin couldn't be converted to a Vec<u8>"),
    )
    .expect("presumed origin exceeded address limits")
  });

  let mut data = output.data();
  let max_data_len = usize::try_from(MAX_DATA_LEN).unwrap();
  if data.len() > max_data_len {
    error!(
      "data in output {} exceeded MAX_DATA_LEN ({MAX_DATA_LEN}): {}. skipping",
      hex::encode(output.id()),
      data.len(),
    );
    return (presumed_origin, None);
  }

  let shorthand = match Shorthand::decode(&mut data) {
    Ok(shorthand) => shorthand,
    Err(e) => {
      info!("data in output {} wasn't valid shorthand: {e:?}", hex::encode(output.id()));
      return (presumed_origin, None);
    }
  };
  let instruction = match RefundableInInstruction::try_from(shorthand) {
    Ok(instruction) => instruction,
    Err(e) => {
      info!(
        "shorthand in output {} wasn't convertible to a RefundableInInstruction: {e:?}",
        hex::encode(output.id())
      );
      return (presumed_origin, None);
    }
  };

  let mut balance = output.balance();
  // Deduct twice the cost to aggregate to prevent economic attacks by malicious miners against
  // other users
  balance.amount.0 -= 2 * N::COST_TO_AGGREGATE;

  (
    instruction.origin.or(presumed_origin),
    Some(InInstructionWithBalance { instruction: instruction.instruction, balance }),
  )
```

**File:** processor/src/multisigs/mod.rs (L934-1007)
```rust
          let (refund_to, instruction) = instruction_from_output::<N>(&output);
          let Some(instruction) = instruction else {
            if let Some(refund_to) = refund_to {
              if let Ok(refund_to) = refund_to.consume().try_into() {
                plans.push(PlanFromScanning::Refund(output.clone(), refund_to));
              }
            }
            continue;
          };

          // Delay External outputs received to new multisig earlier than expected
          if Some(output.key()) == self.new.as_ref().map(|new| new.key) {
            match step {
              RotationStep::UseExisting => {
                DelayedOutputDb::save_delayed_output(txn, &instruction);
                continue;
              }
              RotationStep::NewAsChange |
              RotationStep::ForwardFromExisting |
              RotationStep::ClosingExisting => {}
            }
          }

          instructions.push(instruction);
        }

        // Save the plans created while scanning
        // TODO: Should we combine all of these plans to reduce the fees incurred from their
        // execution? They're refunds and forwards. Neither should need isolate Plan/Eventualities.
        PlansFromScanningDb::set_plans_from_scanning(txn, block_number, plans);

        // If any outputs were delayed, append them into this block
        match step {
          RotationStep::UseExisting => {}
          RotationStep::NewAsChange |
          RotationStep::ForwardFromExisting |
          RotationStep::ClosingExisting => {
            instructions.extend(DelayedOutputDb::take_delayed_outputs(txn));
          }
        }

        let mut block_hash = [0; 32];
        block_hash.copy_from_slice(block.as_ref());
        let mut batch_id = NextBatchDb::get(txn).unwrap_or_default();

        // start with empty batch
        let mut batches = vec![Batch {
          network: N::NETWORK,
          id: batch_id,
          block: BlockHash(block_hash),
          instructions: vec![],
        }];

        for instruction in instructions {
          let batch = batches.last_mut().unwrap();
          batch.instructions.push(instruction);

          // check if batch is over-size
          if batch.encode().len() > MAX_BATCH_SIZE {
            // pop the last instruction so it's back in size
            let instruction = batch.instructions.pop().unwrap();

            // bump the id for the new batch
            batch_id += 1;

            // make a new batch with this instruction included
            batches.push(Batch {
              network: N::NETWORK,
              id: batch_id,
              block: BlockHash(block_hash),
              instructions: vec![instruction],
            });
          }
        }
```
