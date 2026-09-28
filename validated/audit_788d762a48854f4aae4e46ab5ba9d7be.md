### Title
Mismatched hardcoded dust thresholds strand change outputs the processor will never spend - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The external report describes a hardcoded parameter (slippage `0`) that removes caller control and causes funds loss. The analog in Serai is the hardcoded `DUST` constant in `SignableTransaction::new`. The wallet library defines `DUST = 546` (`networks/bitcoin/src/wallet/send.rs:32`), while the Bitcoin network layer defines a separate, larger `DUST = 10_000` (`processor/src/networks/bitcoin.rs:638`). When constructing a transaction with a change output, the wallet only requires the leftover to be `>= 546` sats before emitting it as an output. Any change output with value in `[546, 9_999]` sats is created on-chain to a key the multisig controls, but the processor-side scanner discards every received output below `N::DUST` (`processor/src/multisigs/scanner.rs:564`), and the scheduler/dust-filters in `processor/src/networks/mod.rs:498-530` likewise drop sub-`DUST` balances. The result is an output that is cryptographically spendable by the group key yet permanently invisible to the protocol — effectively locked funds.

### Finding Description
In `SignableTransaction::new`, change handling is:

```rust
// networks/bitcoin/src/wallet/send.rs:223-235
if let Some(change) = change {
  let (weight_with_change, vbytes_with_change) =
    Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
  let fee_with_change = fee_per_vbyte * vbytes_with_change;
  if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
    if value >= DUST {   // DUST == 546
      tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
      ...
    }
  }
}
```

A leftover in `[546, 9_999]` satisfies `value >= DUST` and becomes a real `TxOut` paying to the change script (a `register_offset`-derived P2TR script under `OutputType::Change`). Meanwhile the processor's `get_outputs` → `ScannerEvent::Block` pipeline drops any output with `amount < N::DUST` (`= 10_000`), so the scheduler never learns the output exists and will never aggregate or spend it. The leftover is neither returned via fee accounting as `operating_costs` (the wallet reports it as an output, so `fee()` excludes it) nor recoverable by the protocol.

The depositor/attacker controls `input_sat` by choosing the amount of the Bitcoin transactions they send to the multisig's external/branch/forward addresses (all accepted via `scan_transaction` matching only `script_pubkey`, `networks/bitcoin/src/wallet/mod.rs:199-214`). Combined with scheduled payments of fixed size, they can push `input_sat - payment_sat - fee_with_change` into the dead window, deterministically destroying that residual value.

### Impact Explanation
Permanent loss of user/protocol funds: up to 9,999 sats per crafted transaction are committed into an on-chain output that the processor will never credit or spend. Unlike the external report (where a hardcoded `0` slippage lets swaps execute at arbitrary prices or revert), here a hardcoded constant inconsistent with the consumer's constant causes funds to be emitted into a state the system defines as nonexistent. This matches the "funds reported received that are not spendable" acceptance class: the transaction succeeds, the change output is created, but the scheduler's books never see it.

### Likelihood Explanation
Medium. The dead window is ~9,454 sats wide and `input_sat` is fully attacker-influenced through deposit amounts, so a determined unprivileged party can arrange the condition whenever the processor spends their deposited output alongside a withdrawal. It requires a change-bearing spend to occur, which is routine in the scheduler's flow (`change_address` is always provided by `make_signable_transaction`, `processor/src/networks/bitcoin.rs:446-452`). No validator malice, key leakage, or protocol misuse is required — only public Bitcoin transactions.

### Recommendation
Align the two constants: `SignableTransaction::new` should use the network-level spendability threshold (or accept the minimum change value as a parameter) rather than the relay-rule `DUST = 546`. The wallet comment at `send.rs:27-32` explicitly acknowledges the constant is a simplification "not worth the complexity," but the consuming layer imposes a strictly larger bound, so the wallet must not emit outputs below `N::DUST`. Either parameterize `DUST`, or have `new` return the sub-threshold leftover to the caller as an explicit `NotEnoughChange`/`DustChange` signal so the processor can account it as an operating cost instead of silently creating an unspendable output.

### Proof of Concept
1. Attacker sends a deposit of `D` sats to the multisig external address such that `D` becomes an input to a scheduled spend.
2. A withdrawal of `P` sats is scheduled; `make_signable_transaction` calls `BSignableTransaction::new(inputs, payments, change, fee)`.
3. Choose `D` so `D - P - fee_with_change = 6_000` sats (`>= 546`, `< 10_000`).
4. `SignableTransaction::new` emits `TxOut { value: 6_000, script_pubkey: change_script }` (`send.rs:230`).
5. The signed transaction confirms; `Bitcoin::get_outputs` returns the change `ReceivedOutput`, but the processor scanner drops it at `scanner.rs:564` because `6_000 < N::DUST`.
6. The 6,000 sats are never scheduled, never aggregated, and unrecoverable absent manual intervention — a direct, attacker-influenced loss of funds caused solely by the hardcoded `DUST = 546` mismatch. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L27-32)
```rust
#[rustfmt::skip]
// https://github.com/bitcoin/bitcoin/blob/306ccd4927a2efe325c8d84be1bdb79edeb29b04/src/policy/policy.cpp#L26-L63
// As the above notes, a lower amount may not be considered dust if contained in a SegWit output
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-235)
```rust
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

**File:** processor/src/networks/bitcoin.rs (L624-638)
```rust
    - Each output's value must exceed the fee of the TX spending it at 3000 sat/kilo-vbyte
    The DUST constant needs to be determined by the latter.
    Since these are solely relay rules, and may be raised, we require all outputs be spendable
    under a 5000 sat/kilo-vbyte fee rate.

    5000 sat/kilo-vbyte = 5 sat/vbyte
    5 * 57 = 285 sats/spent-output

    Even if an output took 100 bytes (it should be just ~29-43), taking 400 weight units, adding
    100 vbytes, tripling the transaction size, then the sats/tx would be < 1000.

    Increase by an order of magnitude, in order to ensure this is actually worth our time, and we
    get 10,000 satoshis.
  */
  const DUST: u64 = 10_000;
```

**File:** processor/src/multisigs/scanner.rs (L562-566)
```rust
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
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
  }
```
