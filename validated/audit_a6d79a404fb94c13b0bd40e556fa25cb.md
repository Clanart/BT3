### Title
Operator `withdraw()` Profitability Check Uses Static Config Value While Actual Fee Is Determined Dynamically, Causing Operator BTC Loss on Fee Spikes — (File: core/src/operator.rs)

### Summary

The `withdraw()` function in `core/src/operator.rs` performs a profitability check using the static config value `operator_withdrawal_fee_sats` **before** fetching the live Bitcoin network fee rate. The actual fee deducted from the operator's wallet is determined at execution time by `fund_raw_transaction` and can far exceed `operator_withdrawal_fee_sats`. There is no post-execution check to verify the actual fee stays within the expected bound, so a fee-rate spike (natural or attacker-induced) between the profitability gate and the `fund_raw_transaction` call causes the operator to pay more in fees than the protocol's reimbursement covers, resulting in a net loss of BTC from the operator's tx-sender-managed wallet.

### Finding Description

`Operator::withdraw()` executes in this order:

1. **Profitability gate** (lines 605–612): calls `is_profitable()` which checks `net_profit >= operator_withdrawal_fee_sats`. `operator_withdrawal_fee_sats` is a static operator-configured value intended to cover expected Bitcoin fees. [1](#0-0) 

2. **Live fee-rate fetch** (lines 639–649): queries the Bitcoin node / mempool API for the current fee rate, capped by `fee_rate_hard_cap`. [2](#0-1) 

3. **`fund_raw_transaction`** (lines 652–674): passes the live fee rate to Bitcoin Core, which adds wallet inputs and deducts the actual fee from the operator's wallet. [3](#0-2) 

The comment inside `is_profitable` explicitly acknowledges the design intent:

```
// net profit doesn't take into account the fees, but operator_withdrawal_fee_sats should
net_profit >= operator_withdrawal_fee_sats
``` [4](#0-3) 

There is **no post-check** after `fund_raw_transaction` to verify that the actual fee paid ≤ `operator_withdrawal_fee_sats`. The two values are evaluated at different points in time with no atomicity guarantee.

### Impact Explanation

The operator's economic model is:

```
net = bridge_amount − (out_amount − in_amount) − actual_fees
```

The profitability gate only checks `bridge_amount − (out_amount − in_amount) ≥ operator_withdrawal_fee_sats`. If `actual_fees > operator_withdrawal_fee_sats`, the operator suffers a direct BTC loss from their tx-sender-managed wallet on every such payout. With `fee_rate_hard_cap` potentially set to hundreds or thousands of sat/vB to handle congestion, and a payout transaction weighing ~200–300 vB, the per-withdrawal loss can reach tens of thousands of satoshis. Across many concurrent withdrawals (the protocol supports batched concurrent payouts), the aggregate loss is material.

### Likelihood Explanation

The fee rate is fetched live from the mempool API immediately before `fund_raw_transaction`. Any of the following can cause the actual fee to exceed `operator_withdrawal_fee_sats`:

- Natural mempool congestion between the profitability check and the RPC call.
- An attacker who observes a pending withdrawal RPC call and floods the mempool with high-fee transactions to spike the fee rate before `fund_raw_transaction` executes (a direct analog of AMM front-running).
- A misconfigured `operator_withdrawal_fee_sats` that does not account for the `fee_rate_hard_cap` ceiling.

The `fee_rate_hard_cap` config field exists precisely because fee spikes are expected; it does not prevent the loss — it only bounds it.

### Recommendation

1. **Fetch the fee rate before the profitability check** and pass the same value to both `is_profitable` and `fund_raw_transaction`, eliminating the TOCTOU window.
2. **Add a post-execution fee check**: after `fund_raw_transaction` returns, compute the actual fee from the funded transaction and verify it does not exceed `operator_withdrawal_fee_sats`. Abort and return an error if it does.
3. Alternatively, derive `operator_withdrawal_fee_sats` dynamically from `fee_rate_hard_cap × estimated_tx_vbytes` so the profitability gate always reflects the worst-case fee.

### Proof of Concept

```
1. Operator has operator_withdrawal_fee_sats = 10_000 sat.
2. User submits a valid withdrawal RPC call.
3. is_profitable() passes: net_profit = 50_000 sat ≥ 10_000 sat.
4. Attacker floods mempool; fee rate spikes to fee_rate_hard_cap (e.g. 500 sat/vB).
5. get_fee_rate_kvb() returns 500 sat/vB.
6. fund_raw_transaction() adds wallet inputs; actual fee = 500 × 250 vB = 125_000 sat.
7. Operator's wallet is debited 125_000 sat in fees.
8. Operator's reimbursement covers only bridge_amount; the 115_000 sat excess fee
   (125_000 − 10_000) is an unrecoverable loss from the operator's tx-sender wallet.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** core/src/operator.rs (L503-536)
```rust
    fn is_profitable(
        input_amount: Amount,
        withdrawal_amount: Amount,
        bridge_amount_sats: Amount,
        operator_withdrawal_fee_sats: Amount,
    ) -> bool {
        // Use checked_sub to safely handle potential underflow
        let withdrawal_diff = match withdrawal_amount
            .to_sat()
            .checked_sub(input_amount.to_sat())
        {
            Some(diff) => Amount::from_sat(diff),
            None => {
                // input amount is greater than withdrawal amount, so it's profitable but doesn't make sense
                tracing::warn!(
                    "Some user gave more amount than the withdrawal amount as input for withdrawal"
                );
                return true;
            }
        };

        if withdrawal_diff > bridge_amount_sats {
            return false;
        }

        // Calculate net profit after the withdrawal using checked_sub to prevent panic
        let net_profit = match bridge_amount_sats.checked_sub(withdrawal_diff) {
            Some(profit) => profit,
            None => return false, // If underflow occurs, it's not profitable
        };

        // Net profit must be bigger than withdrawal fee.
        // net profit doesn't take into account the fees, but operator_withdrawal_fee_sats should
        net_profit >= operator_withdrawal_fee_sats
```

**File:** core/src/operator.rs (L598-612)
```rust
        let operator_withdrawal_fee_sats =
            self.config
                .operator_withdrawal_fee_sats
                .ok_or(BridgeError::ConfigError(
                    "Operator withdrawal fee sats is not specified in configuration file"
                        .to_string(),
                ))?;
        if !Self::is_profitable(
            input_utxo.txout.value,
            output_txout.value,
            self.config.protocol_paramset().bridge_amount,
            operator_withdrawal_fee_sats,
        ) {
            return Err(eyre::eyre!("Not enough fee for operator").into());
        }
```

**File:** core/src/operator.rs (L639-674)
```rust
        let fee_rate = self
            .rpc
            .get_fee_rate_kvb(
                self.config.protocol_paramset.network,
                &self.config.mempool_api_host,
                &self.config.mempool_api_endpoint,
                self.config.tx_sender_limits.mempool_fee_rate_multiplier,
                self.config.tx_sender_limits.mempool_fee_rate_offset_sat_kvb,
                self.config.tx_sender_limits.fee_rate_hard_cap,
            )
            .await?;

        // send payout tx using RBF
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;
```
