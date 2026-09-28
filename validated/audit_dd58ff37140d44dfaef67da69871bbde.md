### Title

Cross-Transaction Output Metadata Overwrite Lets an Attacker Hijack Bitcoin Deposit Instructions - ([File: `processor/src/networks/bitcoin.rs`])

### Summary

`Bitcoin::get_outputs` reuses one cumulative `outputs` vector while iterating over every transaction in a block. After processing any transaction, it checks whether that cumulative vector is non-empty and then overwrites the `presumed_origin` and Serai instruction data for all previously collected outputs using the current transaction. Consequently, a later transaction in the same block controls the instruction metadata associated with every earlier multisig output, even if the later transaction never pays the multisig. A crafted final transaction can therefore redirect the credited Serai instruction or refund destination attached to a victim’s Bitcoin deposit.

### Finding Description

`get_outputs` declares `let mut outputs = vec![]` before the transaction loop and appends matching outputs from each transaction to that shared vector. [1](#0-0) 

The subsequent `if outputs.is_empty()` test checks all outputs collected so far, not whether the current transaction produced any outputs. Once any earlier transaction has deposited to the multisig, every later transaction proceeds to the metadata-population path. [2](#0-1) 

That path derives `presumed_origin` from `tx.input[0]`, extracts Serai data from the current transaction, and assigns both values to every accumulated output. [3](#0-2) 

`extract_serai_data` accepts data from any `OP_RETURN` output in the current transaction, so an attacker can supply a valid `Shorthand` instruction in an otherwise unrelated Bitcoin transaction. [4](#0-3) 

The overwritten data is later trusted by `instruction_from_output`, which decodes it as `Shorthand`, converts it into a `RefundableInInstruction`, and pairs the instruction with the victim output’s balance. [5](#0-4) 

External outputs are then converted into batch instructions using that corrupted metadata. [6](#0-5) 

### Impact Explanation

This is a High-severity deposit-authorization flaw. The attacker can cause a victim’s confirmed Bitcoin output to be processed with the attacker’s `Shorthand`, such as a transfer instruction crediting an attacker-controlled Serai address. The multisig retains custody of the victim’s Bitcoin output, while Serai executes the attacker-supplied instruction against the victim’s deposit amount. [7](#0-6) 

If the overwritten data does not produce a valid instruction, the overwritten `presumed_origin` can still redirect the resulting refund to the attacker’s Bitcoin address because invalid external outputs are refunded to the metadata-derived origin. [8](#0-7) 

The analogous authority failure is that Serai authorizes the victim’s deposited value using the origin and instruction from an unrelated transaction, rather than the transaction that actually funded the multisig output. [9](#0-8) 

### Likelihood Explanation

Exploitation requires the attacker’s crafted transaction to be the last transaction processed after the victim deposit within the same confirmed block, or at least to be followed only by transactions whose metadata produces the attacker’s desired result. Because `get_outputs` applies metadata from every later transaction to the accumulated outputs, any subsequent transaction can overwrite the attacker’s values. [10](#0-9) 

An unprivileged Bitcoin user can submit such a transaction publicly, but obtaining the required final block position generally requires favorable ordering, miner cooperation, or control over block construction. This makes the attack less reliable than a purely unconditional exploit, while still producing theft or misdirected refunds when the ordering condition is met. [11](#0-10) 

### Recommendation

Track metadata per transaction rather than per accumulated output vector. Specifically:

- Create a separate `tx_outputs` vector inside the `for tx in &block.txdata[1 ..]` loop.
- Append only `scanner.scan_transaction(tx)` results to `tx_outputs`.
- Skip metadata extraction when `tx_outputs.is_empty()`.
- Populate only `tx_outputs` with that transaction’s `presumed_origin` and `extract_serai_data(tx)`.
- Extend the returned block-level `outputs` from `tx_outputs` after metadata has been assigned.

This ensures each `Output` is bound to the transaction identified by its outpoint, which is already asserted through `output.tx_id() == tx.id()` during collection. [12](#0-11) 

A regression test should place at least two multisig deposits and one unrelated transaction in one block and assert that each output retains the data and origin from its own transaction. [13](#0-12) 

### Proof of Concept

Construct a confirmed Bitcoin block with this ordering:

```text
tx[0]: coinbase
tx[1]: victim transaction
       input:  victim UTXO
       output: BTC amount -> Serai external P2TR address
       output: OP_RETURN = Shorthand::transfer(victim_serai_address)

tx[2]: attacker transaction
       input:  attacker UTXO
       output: OP_RETURN = Shorthand::transfer(attacker_serai_address)
       // No output to Serai is required.
```

When `tx[1]` is scanned, `scanner.scan_transaction` creates the victim’s external `ReceivedOutput` and appends it to `outputs`. [12](#0-11) 

When `tx[2]` is processed, `outputs` is still non-empty even though `tx[2]` produced no multisig output. The function therefore derives `presumed_origin` from the attacker transaction’s first input, extracts the attacker’s `OP_RETURN` payload, and overwrites the victim output’s `data` and `presumed_origin`. [14](#0-13) 

`instruction_from_output` then decodes the attacker’s transfer instruction while using the victim output’s balance, producing an `InInstructionWithBalance` that credits the attacker-defined destination with the victim’s deposit amount. [15](#0-14) 

The same overwrite can instead make the victim instruction invalid while setting the presumed refund origin to the attacker’s input address, causing Serai to schedule return of the victim’s Bitcoin to the attacker. [8](#0-7)

### Citations

**File:** processor/src/networks/bitcoin.rs (L493-505)
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
```

**File:** processor/src/networks/bitcoin.rs (L686-737)
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
```

**File:** processor/src/multisigs/mod.rs (L55-92)
```rust
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

**File:** processor/src/multisigs/mod.rs (L934-958)
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
```
