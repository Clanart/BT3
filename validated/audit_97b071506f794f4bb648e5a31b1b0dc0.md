### Title
Change outputs between Bitcoin's 546-sat dust limit and the network's 10,000-sat spendability threshold are created on-chain yet treated as non-existent, permanently losing funds - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The bug class in the external report is a value being consumed at the wrong scale/precision: the caller divides `collateralAmount` by `precisionDelta * PRECISION` before handing it to a function that expects collateral precision, silently producing 0 and making the operation unusable. Serai's Bitcoin wallet exhibits the same class: `SignableTransaction::new` decides whether to materialize a change output using the raw Bitcoin relay dust limit `DUST = 546`, while the rest of the system defines economically-spendable/scannable outputs with `Bitcoin::DUST = 10_000`. A leftover between 546 and 9,999 sats therefore becomes a real on-chain change output that every consumer assumes was never created.

### Finding Description
In `SignableTransaction::new`, change is emitted whenever the leftover covers the recalculated fee and exceeds the wallet-local dust constant:

- `pub const DUST: u64 = 546;` at `networks/bitcoin/src/wallet/send.rs:32`.
- Change creation at `send.rs:224-235`: `if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) { if value >= DUST { tx_outs.push(...) } }` — the check uses `546`.

Meanwhile the processor-facing contract of the network declares a far larger dust bound that all downstream logic keys off:

- `const DUST: u64 = 10_000;` at `processor/src/networks/bitcoin.rs:638`, justified by the cost to later spend an input (comment at `bitcoin.rs:607-637`).
- The scanner drops any received output below it: `if output.balance().amount.0 >= N::DUST { outputs.push(output); }` at `processor/src/multisigs/scanner.rs:564`.
- `prepare_send` explicitly assumes sub-`N::DUST` change was *not* created and books it as an operating cost: `if on_chain_expected_change < Self::DUST { operating_costs += theoretical_change_amount; }` at `processor/src/networks/mod.rs:589-591`.

So for a plan whose `input_sat - payment_sat - fee_with_change` lands in `[546, 10_000)`:

1. `send.rs` appends a change `TxOut` carrying those sats to the change address (`send.rs:230`).
2. The transaction is signed and broadcast; the change output exists on-chain and is owned by the multisig's change offset key.
3. The scanner never emits it (`scanner.rs:564`), and the scheduler's books recorded it as a burned operating cost (`mod.rs:589-591`).

The sats are irrevocably stranded: no code path will ever schedule them as an input again, since only scanner-emitted `ReceivedOutput`s are spendable. This is exactly the "value in the wrong unit causes downstream logic to treat it as zero" shape of C-01 — a quantity that satisfies one component's threshold is silently zero to the consumer, except here the effect is a real, broadcast loss rather than a revert.

An unprivileged user reaches this path with public inputs: burn/payment amounts are user-chosen, and the leftover change value is directly determined by `sum(inputs) - sum(payments) - fee`, so a payer can engineer the residual into the 546–9,999 sat window on any spend that includes a change address.

### Impact Explanation
Permanent loss of multisig funds on every affected transaction. Each occurrence strands up to 9,999 sats into an output the protocol will never spend nor account for — funds effectively burned while remaining under the multisig's keys. Bounded per-transaction, but repeatable indefinitely and attacker-inducible; analogous to C-01's systematic "works for one scale, silently zero for another" failure. Severity: **Medium**.

### Likelihood Explanation
Any withdrawal plan with change whose residual falls in the ~9.4k-sat window triggers it. Since the attacker controls the payment amount and inputs are known on-chain, hitting the window is straightforward when it is profitable (e.g., to grief the protocol or to trigger the accounting error repeatedly). No privileged position, validator collusion, or exotic state is required — just a burn/payment instruction.

### Recommendation
Use a single authoritative threshold. Either parameterize `SignableTransaction::new`'s change-creation check with the network's `DUST` (10_000) instead of the relay constant `546`, or have callers pass the minimum-change value explicitly. The payment `DustPayment` guard may keep `546` (it governs third-party outputs the protocol still wants to relay-standard), but the *self-owned change* decision must use the same bound the scanner applies, so "wasn't worth creating" and "wasn't emitted" can never diverge.

### Proof of Concept
1. Multisig input set totals `I` sats; a plan contains payments summing to `P` with a change address.
2. Let `vbytes_with_change = V` and fee rate `f`; choose `P` such that `I - P - f*V = 5_000` (within `[546, 10_000)`).
3. `SignableTransaction::new` (send.rs:228-233) computes `value = 5000 >= 546` and pushes a change `TxOut` of 5,000 sats.
4. `prepare_send`/`signable_transaction` succeeds; the TX is signed by FROST and broadcast.
5. In the block containing the TX, `scan_transaction` finds the change output's script, but `scanner.rs:564` drops it because `5_000 < 10_000`.
6. The 5,000 sats are never emitted as a `ReceivedOutput`, never enter any scheduler plan, and are permanently unspendable by the protocol despite sitting under the multisig's change key. `operating_costs` at mod.rs:589-591 records them as consumed, consistent with the (false) belief that the output was never created. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L32-32)
```rust
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

**File:** processor/src/networks/bitcoin.rs (L635-638)
```rust
    Increase by an order of magnitude, in order to ensure this is actually worth our time, and we
    get 10,000 satoshis.
  */
  const DUST: u64 = 10_000;
```

**File:** processor/src/multisigs/scanner.rs (L563-567)
```rust
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
          }
```

**File:** processor/src/networks/mod.rs (L579-591)
```rust
    if change.is_some() {
      let on_chain_expected_change =
        inputs.iter().map(|input| input.balance().amount.0).sum::<u64>() -
          payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>() -
          tx_fee;
      // If the change value is less than the dust threshold, it becomes an operating cost
      // This may be slightly inaccurate as dropping payments may reduce the fee, raising the
      // change above dust
      // That's fine since it'd have to be in a very precarious state AND then it's over-eager in
      // tabulating costs
      if on_chain_expected_change < Self::DUST {
        operating_costs += theoretical_change_amount;
      }
```
