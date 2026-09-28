### Title
Uncheckpointed u64 satoshi arithmetic in `SignableTransaction::new`/`fee()` lets attacker-crafted `ReceivedOutput` values wrap the accounting sanity checks, causing panic or an unspendable signed transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the YieldManager bug — where funds moved to the contract were not reflected in `stakedValue()`, so the `commitYieldReport()` sanity check reverted — `SignableTransaction` performs its balance accounting in raw `u64` sums without checked arithmetic. An unprivileged party who can feed bytes into `ReceivedOutput::read` (listed as an untrusted-bytes API) can craft a `TxOut` value near `u64::MAX`; the `input_sat`/`payment_sat` sums and the `fee()` subtraction then either panic (overflow in debug builds, underflow in `fee()`) or silently wrap (release), defeating the `NotEnoughFunds` sanity check. [1](#0-0) 

### Finding Description
`SignableTransaction::new` computes `input_sat` and `payment_sat` with `.sum::<u64>()`, then checks `input_sat < (payment_sat + needed_fee)` with a plain addition, and later does `input_sat.checked_sub(payment_sat + fee_with_change)` — where the inner `payment_sat + fee_with_change` can itself overflow before `checked_sub` is ever reached. [2](#0-1)  `fee()` computes `sum(inputs) - sum(outputs)` as bare `u64` subtraction, which panics on underflow if outputs exceed inputs. [3](#0-2) 

`ReceivedOutput` is deserialized from untrusted bytes via `ReceivedOutput::read`, and nothing bounds `output.value` to Bitcoin's 21M coin supply. An input whose declared `value` is `u64::MAX` makes `input_sat` wrap/panic; combined with wrapped `payment_sat`, the `NotEnoughFunds` check can pass while `tx_outs` exceed real inputs, producing a consensus-invalid transaction that the FROST multisig in `send.rs` (`multisig`/`TransactionMachine`) will still sign, and for which `fee()` then underflow-panics.

### Impact Explanation
- Debug builds: immediate panic in the `sum::<u64>()` or `payment_sat + needed_fee` arithmetic — a reachable DoS in the transaction-construction path from attacker-supplied bytes.
- Release builds: silent wrapping bypasses the insufficient-funds sanity check, yielding a `SignableTransaction` whose outputs exceed its inputs. The resulting transaction is consensus-invalid (cannot be broadcast), so any orchestration that signs it wastes a FROST signing session and `fee()` panics if queried — permanent stall of that spend attempt.

### Likelihood Explanation
Reachability requires the integrator to pass attacker-deserialized `ReceivedOutput`s (e.g., from gossiped or stored scan results via `ReceivedOutput::read`) into `SignableTransaction::new`. On-chain values are consensus-bounded, so this needs the deserialization path rather than a real deposit; that keeps it below Critical but it is a genuine accounting-check bypass of the same class as the external report.

### Recommendation
- Clamp `ReceivedOutput::read` (or `new`) inputs to `bitcoin::Amount::MAX_MONEY` / 21M sat.
- Use `checked_add`/`checked_sum` for `input_sat`, `payment_sat`, `payment_sat + needed_fee`, and `payment_sat + fee_with_change`, returning `TransactionError::NotEnoughFunds` on overflow.
- Make `fee()` use `checked_sub` and return `Option<u64>`/`Result`, mirroring the report's advice that withdrawn/moved funds must be reflected in the tracked balance before sanity checks run.

### Proof of Concept
```rust
// Deserialize a ReceivedOutput with value = u64::MAX via ReceivedOutput::read
let mut buf = Vec::new();
// ... valid ReceivedOutput encoding with TxOut.value = u64::MAX ...
let fake = ReceivedOutput::read::<&[u8]>(&mut buf.as_slice()).unwrap();

// input_sat wraps: u64::MAX + dust-value real input wraps to ~small
let real = scanner.scan_transaction(&tx).pop().unwrap();
// Debug: panics inside sum::<u64>(); Release: input_sat wraps low
let res = SignableTransaction::new(vec![fake, real], &payments, None, None, 1);
// With payments near u64::MAX: payment_sat + needed_fee wraps, NotEnoughFunds
// check passes, tx_outs > inputs => tx.fee() underflow-panics.
```
Relevant code: `input_sat`/`payment_sat` sums and `payment_sat + needed_fee` at [4](#0-3) , `input_sat.checked_sub(payment_sat + fee_with_change)` at [5](#0-4) , and the unchecked subtraction in `fee()` at [3](#0-2) .

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-235)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();

    // Add the OP_RETURN output
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
    }
```
