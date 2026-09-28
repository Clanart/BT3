### Title
Multi-input Bitcoin deposits can be refunded to the wrong origin account - (File: `processor/src/networks/bitcoin.rs`)

### Summary
Bitcoin output discovery assigns a single presumed origin to every relevant output in a transaction by inspecting only `tx.input[0]`. In a transaction funded by multiple parties, Serai can therefore attribute all deposited outputs to whichever account funded the first input. When an external deposit cannot be converted into a valid instruction, that incorrect origin is used as the refund destination.

### Finding Description
`Bitcoin::get_outputs` scans each transaction for outputs belonging to the multisig and collects them in `outputs`. It then derives one `presumed_origin` from the script of the output spent by only `tx.input[0]`. Finally, it assigns that same origin to every scanned output in the transaction [1](#0-0) .

The downstream processing path treats this field as a usable refund/account identity. `instruction_from_output` converts `presumed_origin` into an `ExternalAddress`, and uses it as the origin if the embedded instruction does not explicitly override it [2](#0-1) [3](#0-2) . If the deposit has no valid instruction, the multisig schedules a refund plan paying `refund_to` the full output balance [4](#0-3) [5](#0-4) .

This is a concrete “wrong account” selection: the transaction may contain inputs from several independent Bitcoin accounts, but Serai unconditionally chooses input zero’s previous output address for every Serai-bound output.

### Impact Explanation
An attacker participating in a multi-input deposit transaction can place their own input at index zero and cause another participant’s contribution to be attributed to the attacker’s Bitcoin address. If the transaction contains malformed, oversized, or otherwise unusable Serai instruction data, Serai emits a refund plan for the resulting external output and pays that refund to the attacker-controlled first-input address rather than the account that actually funded the output.

This can result in the victim’s contributed funds being refunded to the attacker. Even for valid instructions, relying on input zero as the sole origin can incorrectly associate deposits with the wrong external account when the instruction omits an explicit origin.

### Likelihood Explanation
The attack requires a transaction with multiple independently funded inputs, which is less common than a normal single-wallet deposit. However, such transactions are reachable through ordinary Bitcoin transaction data and commonly arise in PayJoin, CoinJoin, batching, custodial aggregation, and collaboratively constructed PSBT flows. An unprivileged participant can control input ordering in a jointly constructed transaction without compromising a validator, node, peer, RPC endpoint, or key share.

### Recommendation
Do not derive a single authoritative origin from `tx.input[0]` for all outputs.

Possible mitigations include:

- Require external deposits to specify an explicit refund/origin address in their instruction data.
- Treat multi-input transactions as having an ambiguous origin unless all inputs resolve to the same address or a clearly documented ownership policy.
- Reject or avoid automatic refunds when the origin is ambiguous.
- If automatic refunds remain supported, derive refunds from a policy that cannot be manipulated by input ordering, rather than assuming input zero represents all contributors.

At minimum, `get_outputs` should mark `presumed_origin` as unavailable for transactions whose inputs cannot all be established as belonging to the same account.

### Proof of Concept
1. A victim and attacker collaboratively construct a Bitcoin transaction with two funded inputs:
   - `tx.input[0]` spends a UTXO whose script resolves to the attacker’s address.
   - `tx.input[1]` spends a UTXO whose script resolves to the victim’s address.
2. The transaction creates a Serai external output and carries malformed or unusable instruction data.
3. `Bitcoin::get_outputs` scans the Serai output, reads only `tx.input[0]`, fetches its previous output, and sets the attacker’s address as the output’s `presumed_origin` [6](#0-5) .
4. `instruction_from_output` returns the attacker’s address as the refund origin because the instruction is invalid or omitted [7](#0-6) .
5. The scanner creates a `PlanFromScanning::Refund` for the output, and the UTXO scheduler constructs a plan paying the output balance to the attacker address [4](#0-3) [8](#0-7) .
6. After threshold signing and confirmation, the refund transaction pays the attacker even though part of the deposited transaction value came from the victim’s input.

### Citations

**File:** processor/src/networks/bitcoin.rs (L691-736)
```rust
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

**File:** processor/src/multisigs/mod.rs (L43-53)
```rust
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
```

**File:** processor/src/multisigs/mod.rs (L55-91)
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

**File:** processor/src/multisigs/scheduler/utxo.rs (L587-601)
```rust
  fn refund_plan<D: Db>(
    &mut self,
    _: &mut D::Transaction<'_>,
    output: N::Output,
    refund_to: N::Address,
  ) -> Plan<N> {
    let output_id = output.id().as_ref().to_vec();
    let res = Plan {
      key: output.key(),
      // Uses a payment as this will still be successfully sent due to fee amortization,
      // and because change is currently always a Serai key
      payments: vec![Payment { address: refund_to, data: None, balance: output.balance() }],
      inputs: vec![output],
      change: None,
      scheduler_addendum: (),
```
