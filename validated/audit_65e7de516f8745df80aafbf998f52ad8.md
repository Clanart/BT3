### Title
Missing upper-bound / deviation check on block-derived fee rate allows attacker to burn multisig funds as miner fees or stall payouts - (File: processor/src/networks/bitcoin.rs)

### Summary
Analogous to the missing Chainlink circuit-breaker/deviation check, `Bitcoin::median_fee` accepts an untrusted, externally influenced value (the median fee rate of a single Bitcoin block) with only a lower bound (`fee.max(1)`) and no upper bound or deviation check. The resulting fee rate is fed directly into `SignableTransaction::new` as `fee_per_vbyte`, where it determines how much of the multisig's funds are burned as transaction fees — or whether the plan is dropped entirely.

### Finding Description
`median_fee` (processor/src/networks/bitcoin.rs:388-415) computes a fee rate as the median of `in_value - out_value` / `vsize` over every non-coinbase transaction in a single block fetched via `get_block`. The only sanity check applied is `fee.max(1)` — enforcing a minimum of 1 sat/vbyte, but no maximum and no deviation check against a previously known or expected value:

```rust
fees.sort();
let fee = fees.get(fees.len() / 2).copied().unwrap_or(0);
Ok(Fee(fee.max(1)))
```

This mirrors the reported bug class: an oracle-like input is only validated against a floor, never against a ceiling or against deviation from expected values. The fee rate returned is then used in `make_signable_transaction` (line 431, 451) and passed as `fee_per_vbyte` to `BSignableTransaction::new`, where `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) is subtracted from inputs alongside payments. If the plan uses change, `input_sat - payment_sat - fee_with_change` must be ≥ DUST; if there is no change output, the entire leftover is paid as fee. If `needed_fee` is inflated beyond what the inputs cover, `NotEnoughFunds` is returned, `prepare_send` treats the plan as unfulfillable, and its branch payments are dropped (processor/src/networks/mod.rs:442-455).

### Impact Explanation
An attacker who causes a block used for fee estimation to contain predominantly high-fee transactions can skew the median arbitrarily. Two concrete harms:

1. **Fee burn**: If inputs cover the inflated fee, the multisig signs a transaction paying an attacker-influenced, arbitrarily large fee to miners — up to `sum(inputs) - sum(payments)` with no cap.
2. **Payment drops / fund lockup**: If the inflated fee makes the plan unfulfillable, `prepare_send` drops all branch payments in the plan (mod.rs:448 `drop_branches`), stalling or destroying scheduled payouts.

This reaches production code from purely public inputs: the attacker only needs to broadcast ordinary high-fee Bitcoin transactions and have them mined, which any fee-paying user can induce.

### Likelihood Explanation
Skewing a block's median requires the attacker's transactions to occupy roughly half the block's tx count. This is expensive but feasible: an attacker can flood the mempool with self-paying transactions at an extreme feerate; miners order by feerate, so a block can be dominated by attacker transactions during low-activity periods. Even without a targeted attack, organically anomalous blocks (fee spikes, consolidation storms) are accepted with no deviation check — the same "outlier accepted as valid" weakness flagged in the report. Severity is Medium: real economic loss, but costly/preconditioned.

### Recommendation
- Cap the fee rate against a hard maximum (e.g., a configured ceiling or a multiple of `DEFAULT_MIN_RELAY_TX_FEE`).
- Add a deviation check: compare the new median against a cached/recent fee (the code already has a `TODO2` at line 429 noting the intent to use a multi-block cached fee); reject or clamp values deviating beyond a threshold.
- Average over several blocks rather than a single block to reduce median manipulability.
- On out-of-range fees, pause/retry rather than dropping branch payments.

### Proof of Concept
Conceptual trace:
1. Attacker broadcasts ~300 transactions paying e.g. 100,000 sat/vbyte, getting them mined in block N used as `block_number`.
2. `median_fee` returns `Fee(100_000)` — only checked `>= 1`.
3. `make_signable_transaction` calls `BSignableTransaction::new(..., fee.0)`; `needed_fee = 100_000 * vbytes`.
4. Either inputs cover it and the multisig signs a TX burning `needed_fee` to miners, or `NotEnoughFunds` → `prepare_send` drops all payments in the plan. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** processor/src/networks/bitcoin.rs (L388-415)
```rust
  async fn median_fee(&self, block: &Block) -> Result<Fee, NetworkError> {
    let mut fees = vec![];
    if block.txdata.len() > 1 {
      for tx in &block.txdata[1 ..] {
        let mut in_value = 0;
        for input in &tx.input {
          let mut input_tx = input.previous_output.txid.to_raw_hash().to_byte_array();
          input_tx.reverse();
          in_value += self
            .rpc
            .get_transaction(&input_tx)
            .await
            .map_err(|_| NetworkError::ConnectionError)?
            .output[usize::try_from(input.previous_output.vout).unwrap()]
          .value
          .to_sat();
        }
        let out = tx.output.iter().map(|output| output.value.to_sat()).sum::<u64>();
        fees.push((in_value - out) / u64::try_from(tx.vsize()).unwrap());
      }
    }
    fees.sort();
    let fee = fees.get(fees.len() / 2).copied().unwrap_or(0);

    // The DUST constant documentation notes a relay rule practically enforcing a
    // 1000 sat/kilo-vbyte minimum fee.
    Ok(Fee(fee.max(1)))
  }
```

**File:** processor/src/networks/bitcoin.rs (L429-452)
```rust
    // TODO2: Use an fee representative of several blocks, cached inside Self
    let block_for_fee = self.get_block(block_number).await?;
    let fee = self.median_fee(&block_for_fee).await?;

    let payments = payments
      .iter()
      .map(|payment| {
        (
          payment.address.clone().into(),
          // If we're solely estimating the fee, don't specify the actual amount
          // This won't affect the fee calculation yet will ensure we don't hit a not enough funds
          // error
          if calculating_fee { Self::DUST } else { payment.balance.amount.0 },
        )
      })
      .collect::<Vec<_>>();

    match BSignableTransaction::new(
      inputs.iter().map(|input| input.output.clone()).collect(),
      &payments,
      change.clone().map(Into::into),
      None,
      fee.0,
    ) {
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-221)
```rust
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
```

**File:** processor/src/networks/mod.rs (L442-455)
```rust
    let Some(tx_fee) = self.needed_fee(block_number, &inputs, &payments, &change).await? else {
      // This Plan is not fulfillable
      // TODO: Have Plan explicitly distinguish payments and branches in two separate Vecs?
      return Ok(PreparedSend {
        tx: None,
        // Have all of its branches dropped
        post_fee_branches: drop_branches(key, &payments),
        // This plan expects a change output valued at sum(inputs) - sum(outputs)
        // Since we can no longer create this change output, it becomes an operating cost
        // TODO: Look at input restoration to reduce this operating cost
        operating_costs: operating_costs +
          if change.is_some() { theoretical_change_amount } else { 0 },
      });
    };
```
