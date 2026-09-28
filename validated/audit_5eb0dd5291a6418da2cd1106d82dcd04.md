### Title
Bitcoin scanner applies later transactions’ origin and instruction data to earlier deposits - ([File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary
`Bitcoin::get_outputs` accumulates every matching output in a block into a single `outputs` vector, then treats that block-scoped vector as belonging to the transaction currently being processed. Any later non-coinbase transaction in the same block causes all previously collected outputs to have their `presumed_origin` and, for `External` outputs, instruction `data` overwritten with the later transaction’s values. An unprivileged Bitcoin user can therefore attach an arbitrary transaction after a victim’s Serai deposit and erase or replace the deposit’s instruction and refund origin. [1](#0-0) 

### Finding Description
`Scanner::scan_transaction` correctly returns a `ReceivedOutput` only when an output’s `script_pubkey` matches a registered multisig script. [2](#0-1) 

The processor then stores those outputs in `outputs`, which is declared before iterating over all non-coinbase transactions. For every transaction, it checks whether `outputs` is non-empty rather than whether the current transaction produced outputs. Consequently, once any transaction has produced a Serai output, every subsequent transaction executes the metadata-population block. [3](#0-2) 

That block derives `presumed_origin` from `tx.input[0]`, extracts instruction data from the current transaction, and assigns both values to every element in `outputs`, including outputs created by earlier transactions. [4](#0-3) 

Downstream, `instruction_from_output` decodes `output.data()` as a `Shorthand` and converts it into a `RefundableInInstruction`. If decoding fails, it still returns `presumed_origin`. [5](#0-4)  The caller treats an external output with no valid instruction but a presumed origin as refundable and creates `PlanFromScanning::Refund(output, refund_to)`. [6](#0-5) 

### Impact Explanation
An attacker can associate their own input address or instruction payload with a victim’s Bitcoin deposit. If the attacker’s later transaction carries no Serai instruction, `extract_serai_data` returns empty data, erasing a valid instruction already assigned to the victim’s output and leaving the attacker-controlled `presumed_origin`. The deposit can then be scheduled as a refund to the attacker instead of executing the victim’s intended instruction. [7](#0-6) [6](#0-5) 

Alternatively, the later transaction can contain a valid attacker-controlled `Shorthand`, causing the victim’s funds to be processed under the attacker’s instruction. This permits theft or redirection of the economic effect of a deposit without compromising the threshold key or producing an invalid Bitcoin transaction.

### Likelihood Explanation
Exploitation requires the attacker’s transaction to appear later in `block.txdata` than the victim’s deposit and within a block scanned after `Bitcoin::CONFIRMATIONS`. The attacker needs no validator privileges, malformed encodings, key material, or invalid chain data; ordinary Bitcoin transactions are sufficient. The principal constraint is influencing same-block transaction ordering, so the attack is more reliable when the attacker mines the block, pays for inclusion ordering, or otherwise observes and targets a pending deposit.

### Recommendation
Keep per-transaction outputs separate from the block-level result. `get_outputs` should collect `tx_outputs` for only the current transaction, skip metadata enrichment when that vector is empty, populate only `tx_outputs`, and then append them to the block-level result. The implementation should also extract the origin and data only from the same transaction that created each output, rather than from whichever transaction is currently being iterated. Add a regression test with a Serai deposit followed by an unrelated attacker transaction in the same block, asserting the deposit retains its own origin and data.

### Proof of Concept
Construct a confirmed block with this ordering:

```text
txdata[0]: coinbase

txdata[1]: victim deposit
  input[0]:  victim-controlled UTXO
  output[0]: >= Bitcoin::DUST paid to Serai's External P2TR script
  output[1]: OP_RETURN encoding a victim-controlled Shorthand / refund origin

txdata[2]: attacker transaction
  input[0]:  attacker-controlled UTXO
  output[]:  arbitrary outputs, with no Serai deposit and no OP_RETURN
```

During `get_outputs`, `txdata[1]` appends the victim’s external output to the block-level `outputs` vector. When `txdata[2]` is processed, `outputs` is still non-empty even though `txdata[2]` produced no matching output. The processor therefore derives `presumed_origin` from `txdata[2].input[0]` and calls `extract_serai_data(txdata[2])`, which returns empty data if there is no matching OP_RETURN or witness payload. Both values are then assigned to the victim’s earlier output. [8](#0-7) 

When the scanner event is handled, the victim output now has attacker-controlled `presumed_origin` and empty `data`. `Shorthand::decode` fails, and the attacker’s presumed origin causes the victim’s deposit to be scheduled as `PlanFromScanning::Refund` to the attacker. [9](#0-8) [6](#0-5)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
```rust
  /// Scan a transaction.
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
```

**File:** processor/src/multisigs/mod.rs (L55-82)
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
```

**File:** processor/src/multisigs/mod.rs (L934-940)
```rust
          let (refund_to, instruction) = instruction_from_output::<N>(&output);
          let Some(instruction) = instruction else {
            if let Some(refund_to) = refund_to {
              if let Ok(refund_to) = refund_to.consume().try_into() {
                plans.push(PlanFromScanning::Refund(output.clone(), refund_to));
              }
            }
```
