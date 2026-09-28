### Title
Fee/vsize calculation ignores the OP_RETURN `data` output appended before `calculate_weight_vbytes`, understating `needed_fee` and inflating the change output - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends an extra `TxOut` (the OP_RETURN `data` output) to `tx_outs` before computing the transaction's weight/vbytes, yet `calculate_weight_vbytes` is invoked with only `payments` — the pre-mutation output list. The recorded `needed_fee` and the change output value are therefore computed against a denominator that no longer reflects the transaction being built, the same bug class as the reference report (a metric recorded before accounting for state changed by an intermediate side effect).

### Finding Description
In `SignableTransaction::new`:

1. `tx_outs` is built from `payments` and then an OP_RETURN output carrying caller-supplied `data` (up to 80 bytes) is pushed onto it (lines 194–202).
2. The fee is then estimated with `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204) — `payments`, not `tx_outs`. The extra output's script (10 bytes overhead + up to 80 bytes pushdata) is never included in `weight`/`vbytes`.
3. The change path recomputes with `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (line 226), which still excludes the OP_RETURN output.
4. The change output value is then set to `input_sat - payment_sat - fee_with_change` (line 228), where `fee_with_change` is understated.

So `needed_fee` is recorded using a stale view of the output set after a mutation (the OP_RETURN push) enlarged it — directly analogous to `navPerShareHighMark` being recorded using `totalSupply` before the fee-mint increased it.

### Impact Explanation
- `needed_fee` under-reports the fee required to reach `fee_per_vbyte` by `fee_per_vbyte * ceil(op_return_size / 4)` vbytes (up to ~24 vbytes for 80-byte data).
- The `NotEnoughFunds` check (line 215) can pass when the TX cannot actually pay the intended rate.
- When a change address is set, the change output is credited `fee_with_change` too much, so the *actual* fee (`sum(inputs) - sum(outputs)`, per `fee()`) falls below the requested fee rate and can fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the minimum-fee check passed. The signed transaction may then fail to relay/confirm, stalling a spend of multisig funds — including spends embedding user-supplied transfer memo data.

### Likelihood Explanation
The flaw is deterministic whenever `data.is_some()`. In-tree the sole production caller (`processor/src/networks/bitcoin.rs::make_signable_transaction`, line 446) passes `None`, so the impact is only realized if/when a caller attaches OP_RETURN data — the test suite and the `Shorthand::transfer`/`InInstruction` flow (which encodes user-controlled memo bytes, e.g., `tests/full-stack/src/tests/mint_and_burn.rs` lines 315–320) demonstrate exactly that intended use. Likelihood is moderate; impact is bounded to fee-rate shortfall/stuck transactions rather than fund theft, consistent with Medium.

### Recommendation
Compute weight/vbytes against the actual output list. Pass `&tx_outs`-equivalent (i.e., `payments` plus the OP_RETURN output's `script_pubkey`) into `calculate_weight_vbytes`, or refactor `calculate_weight_vbytes` to take the final `Vec<TxOut>` and recompute after every output is pushed, so `needed_fee` is recorded only after the output set is finalized.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
let data = vec![0xAA; 80]; // max-allowed OP_RETURN payload
let tx = SignableTransaction::new(
  vec![input],                       // one input
  &payments,                       // e.g., one payment
  Some(change_script),             // change address
  Some(data.clone()),              // OP_RETURN output
  fee_per_vbyte,
).unwrap();

// tx.output has payments + OP_RETURN + change
// needed_fee was computed from vbytes(payments + change) only:
//   calculate_weight_vbytes(n_in, payments, Some(&change))  // line 226
// The real vsize includes ~ +23 vbytes for the OP_RETURN output,
// so tx.fee() / tx.vsize() < fee_per_vbyte, and change was overpaid
// by fee_per_vbyte * op_return_vbytes.
```
Concretely: with `fee_per_vbyte = 1` and `data.len() = 80`, the OP_RETURN output adds ~91 bytes (~23 vbytes). `needed_fee`/`fee_with_change` omit it, so the change output is ~23 sat larger than intended and the broadcast TX pays ~23 sat less than the requested rate. Setting `input_sat = payment_sat + needed_fee` exactly (no change) likewise yields a signed TX below the intended fee rate despite `NotEnoughFunds` passing. [1](#0-0)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L193-235)
```rust
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
