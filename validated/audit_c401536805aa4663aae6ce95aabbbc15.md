### Title
Cross-transaction Bitcoin metadata contamination redirects external deposits - ([File: processor/src/networks/bitcoin.rs](https://github.com/serai-dex/serai/blob/develop/processor/src/networks/bitcoin.rs))

### Summary
`Bitcoin::get_outputs` applies the origin address and Serai instruction data from the current transaction to every previously accumulated output in the block, including outputs produced by unrelated earlier transactions. This lets an unprivileged Bitcoin sender overwrite the effective `copyfrom`-style provenance and instruction metadata of another user's deposit by ordering any attacker-controlled transaction after it in the same block. [1](#0-0) 

### Finding Description
`get_outputs` accumulates all scan results in `outputs`, but the `if outputs.is_empty()` check tests that cumulative vector rather than the results of the current transaction. Consequently, after any matching output has been found, every subsequent non-coinbase transaction causes `presumed_origin` and `data` to be recalculated and assigned to all accumulated outputs, even when the current transaction contains no Serai output. [2](#0-1) 

The provenance value is taken from the script of the output spent by `tx.input[0]`, while instruction data is independently extracted from any OP_RETURN output or supported witness pattern. An attacker can therefore choose the reported origin by spending their own UTXO and choose the reported instruction payload with their own transaction data. [3](#0-2) [4](#0-3) 

Downstream, `instruction_from_output` trusts `output.data()` and `output.presumed_origin()`, either processing the decoded instruction or refunding to the explicit/presumed origin. [5](#0-4)  The scanner event handler then creates an instruction or a `PlanFromScanning::Refund` from that contaminated metadata. [6](#0-5) 

### Impact Explanation
An attacker can cause the multisig to treat a victim's deposit as though it carried attacker-selected instruction data or originated from the attacker's Bitcoin address. In the simplest case, overwriting the deposit's data with empty or malformed data triggers the refund path, while overwriting `presumed_origin` makes the refund destination an attacker-controlled address, resulting in honest validators signing an unintended transaction that transfers the victim's funds to the attacker. [3](#0-2) [7](#0-6) 

### Likelihood Explanation
Exploitation requires the attacker's transaction to appear later in the same block as the victim's deposit; it does not need to send funds to Serai or know any secret. Once that ordering occurs, the overwrite is deterministic and the attacker only needs to broadcast a valid transaction spending their own UTXO, optionally with an OP_RETURN payload. [8](#0-7) 

### Recommendation
Keep the outputs returned by `scanner.scan_transaction(tx)` in a per-transaction local vector and populate `presumed_origin`/`data` only for that vector. Skip metadata processing when the current transaction produced no matching outputs, rather than when the cumulative block-level vector is empty. [9](#0-8) 

### Proof of Concept
1. A victim broadcasts `tx_v`, which creates an output paying the multisig's external P2TR script.
2. The attacker broadcasts `tx_a`, ordered after `tx_v` in the same block, spending the attacker's UTXO and optionally containing malformed or attacker-selected OP_RETURN data.
3. `tx_a` does not pay any scanner-registered script.
4. During `get_outputs`, `scan_transaction(tx_v)` records the victim output; `scan_transaction(tx_a)` records nothing, but the nonempty cumulative `outputs` vector still enters the metadata-population path.
5. The victim output's `presumed_origin` becomes the address of the attacker's spent output, and its `data` becomes the attacker transaction's data or empty data. [2](#0-1) 
6. With invalid/empty data, `instruction_from_output` returns no instruction but returns the attacker-controlled `presumed_origin`; the scanner then creates a refund plan to that address, causing the victim's deposit to be signed away to the attacker. [7](#0-6) [6](#0-5)

### Citations

**File:** processor/src/networks/bitcoin.rs (L493-523)
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
```

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

**File:** processor/src/multisigs/mod.rs (L40-92)
```rust
fn instruction_from_output<N: Network>(
  output: &N::Output,
) -> (Option<ExternalAddress>, Option<InInstructionWithBalance>) {
  assert_eq!(output.kind(), OutputType::External);

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
