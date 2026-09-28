### Title
Bitcoin deposit refunds are sent to the owner of `tx.input[0]`, not the actual depositor — a co-transacting attacker can steal refunds - (File: processor/src/networks/bitcoin.rs)

### Summary
Analogous to the Cooler `rescindRequest` bug — where collateral was returned to the contract owner rather than the party that deposited it — Serai's Bitcoin processor attributes a deposit's `presumed_origin` (the address refunds are paid to) to the spender of `tx.input[0]`, regardless of who actually funded the output to Serai. An unprivileged party who co-funds or assembles a transaction paying Serai's external address can position their own UTXO at input index 0 and divert the refund of other participants' funds to themselves.

### Finding Description
In `Bitcoin::get_outputs`, when a transaction contains outputs paying a registered Serai script, the processor computes a single `presumed_origin` for the entire transaction from `tx.input[0]` alone: it fetches the previous output of the *first* input and uses its `script_pubkey` as the origin address. That same `presumed_origin` is then stamped onto *every* Serai-bound output in the transaction. [1](#0-0) 

Downstream, `instruction_from_output` turns this into the refund destination: if the embedded `Shorthand`/`RefundableInInstruction` fails to decode, isn't convertible, or the instruction itself omits `origin` (`instruction.origin.or(presumed_origin)`), a `PlanFromScanning::Refund` is built paying `refund_to` — i.e., the input[0] owner. [2](#0-1) [3](#0-2) [4](#0-3) 

Bitcoin transactions routinely have inputs owned by distinct parties (PayJoin, CoinJoin, exchange/broker batch withdrawals, PSBT-assembled payments). Nothing in the protocol ties input index 0 to the funder of a given output. An attacker who contributes or places their UTXO at `input[0]` while a victim's input funds the output to Serai causes Serai to attribute the deposit to the attacker. If the instruction fails or was crafted to fail (the attacker can even provide the OP_RETURN/witness-embedded `Shorthand` via `extract_serai_data`, which is applied uniformly to all outputs), the entire deposited balance — minus only `2 * COST_TO_AGGREGATE` — is refunded to the attacker's address. [5](#0-4) 

The root cause is identical in shape to the Cooler report: the refund path hardcodes a recipient (`owner()` there; `tx.input[0]`'s spender here) that is not verified to be the party that deposited the collateral.

### Impact Explanation
An unprivileged attacker steals bridged deposits. When a deposit's instruction fails (invalid shorthand, oversized data, reverting Dex call) or omits an explicit `origin`, Serai schedules a refund to the presumed origin. Because the presumed origin is derived only from `tx.input[0]`, the attacker — not the depositor — receives the refunded coins. For a victim deposit of arbitrary size routed through a batched/co-signed transaction, this is a direct loss of the full deposit amount.

### Likelihood Explanation
Multi-party funding transactions are common Bitcoin patterns (batching services, PayJoin, brokered on-ramps). The attacker only needs to send/participate in a Bitcoin transaction — a fully public input. Conditions required: the deposit instruction fails or omits `origin`, and the attacker controls the input at index 0. Both are within an attacker's influence when they assemble or co-sign the transaction, or when they are the entity the depositor uses to relay funds. Likelihood is moderate; severity is High due to direct fund theft.

### Recommendation
Do not treat `tx.input[0]` as authoritative origin. Either:
- Require an explicit `origin` in the `RefundableInInstruction` for Bitcoin deposits and refuse to refund (burn/hold) when it's absent, or
- Attribute the refund to the input(s) proportionally to their contributed value toward the Serai output (still heuristic — explicit origin is safer), or
- Per spec, only use the input-derived `presumed_origin` as a fallback when the transaction has exactly one input, or all inputs share a common script type controlled by one party.

### Proof of Concept
1. Attacker A and victim V co-create a transaction (e.g., A is a relay/batcher, or A contributes a PayJoin input). The tx has `input[0]` = A's UTXO, `input[1]` = V's 1 BTC UTXO, and an output paying ~1 BTC to Serai's external P2TR address. A crafts/attaches an OP_RETURN `Shorthand` that fails `RefundableInInstruction::try_from` (or V's instruction is invalid/omits `origin`).
2. `get_outputs` scans the output, computes `presumed_origin` from `tx.input[0]` → A's script_pubkey, and stamps it on the deposit.
3. `instruction_from_output` fails to decode the instruction and returns `(Some(A's address), None)`.
4. The processor pushes `PlanFromScanning::Refund(output, A)`, and the multisig signs a transaction paying V's ~1 BTC deposit to A.

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

**File:** processor/src/networks/bitcoin.rs (L706-736)
```rust
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

**File:** processor/src/multisigs/mod.rs (L84-92)
```rust
  let mut balance = output.balance();
  // Deduct twice the cost to aggregate to prevent economic attacks by malicious miners against
  // other users
  balance.amount.0 -= 2 * N::COST_TO_AGGREGATE;

  (
    instruction.origin.or(presumed_origin),
    Some(InInstructionWithBalance { instruction: instruction.instruction, balance }),
  )
```

**File:** processor/src/multisigs/mod.rs (L903-907)
```rust
              } else if let Some(refund_to) = refund_to {
                if let Ok(refund_to) = refund_to.consume().try_into() {
                  // Build a dedicated Plan refunding this
                  plans.push(PlanFromScanning::Refund(output.clone(), refund_to));
                }
```

**File:** processor/src/multisigs/mod.rs (L934-941)
```rust
          let (refund_to, instruction) = instruction_from_output::<N>(&output);
          let Some(instruction) = instruction else {
            if let Some(refund_to) = refund_to {
              if let Ok(refund_to) = refund_to.consume().try_into() {
                plans.push(PlanFromScanning::Refund(output.clone(), refund_to));
              }
            }
            continue;
```
