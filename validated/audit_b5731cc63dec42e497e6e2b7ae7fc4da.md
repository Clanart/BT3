### Title

Unbounded Bitcoin fee estimation allows externally mined transactions to impose excessive multisig fees - ([File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary

The Bitcoin processor derives `fee_per_vbyte` directly from the upper median fee rate of all non-coinbase transactions in a single block, with no upper bound or outlier rejection. That attacker-influenced rate is passed unchanged into `SignableTransaction::new`, where it is multiplied by the transaction’s virtual size and only checked against a minimum relay fee and available funds. Consequently, sufficiently many high-fee public Bitcoin transactions can cause Serai to construct a spend transaction paying an arbitrarily large fee relative to its intended economics. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description

`Bitcoin::median_fee` iterates over every non-coinbase transaction in the selected block, computes `(input_value - output_value) / vsize`, sorts the rates, and selects `fees[len / 2]`, returning at least `1` sat/vbyte. No maximum fee rate, percentile cap, sanity bound, or multi-block smoothing is applied. [1](#0-0) 

`make_signable_transaction` obtains the block at `block_number`, calls `median_fee`, and passes `fee.0` directly as the `fee_per_vbyte` argument to `bitcoin_serai::wallet::SignableTransaction::new`. [2](#0-1) 

`SignableTransaction::new` computes `needed_fee = fee_per_vbyte * vbytes` and rejects only fees below the Bitcoin minimum relay fee or fees exceeding the selected inputs. It has no maximum-fee constraint. [3](#0-2) 

The resulting fee is then propagated through `prepare_send`: `needed_fee` returns the calculated `tx_fee`, payment amounts are reduced to amortize it, and `signable_transaction` constructs the transaction with the same fee oracle input. [4](#0-3) [5](#0-4) 

### Impact Explanation

An unprivileged Bitcoin user can broadcast transactions carrying unusually high fees. Once those transactions occupy the selected upper-median position of the block used for estimation, Serai treats their fee rate as authoritative for its own threshold-signed spend. For a transaction of `V` vbytes and attacker-influenced rate `R`, Serai commits to approximately `R * V` satoshis in transaction fees. [1](#0-0) [6](#0-5) 

If the plan has sufficient input and payment value, the transaction is still accepted because the code only verifies `input_sat >= payment_sat + needed_fee`. The excessive fee is amortized from user payments or charged against the plan, producing a valid but economically distorted transaction eligible for threshold signing. [7](#0-6) [8](#0-7) 

### Likelihood Explanation

The attacker does not need validator access, RPC access, private keys, or malformed cryptographic inputs. They only need to cause enough high-fee transactions to occupy the upper-median position of the block selected by `make_signable_transaction`; for `n` non-coinbase transactions, approximately `ceil(n / 2)` entries must be at least the target rate. [9](#0-8) 

The attack requires paying or arranging abnormal transaction fees and timing them with a Serai plan, so it is not cost-free. However, the vulnerable path uses a single block’s transactions as the entire oracle and places no bound on the resulting rate, making the likelihood and impact consistent with a medium-severity economic manipulation. [2](#0-1) [3](#0-2) 

### Recommendation

Define a protocol-level `MAX_FEE_PER_VBYTE` and reject or clamp fee estimates above it. Use checked arithmetic for `fee_per_vbyte * vbytes`, and preferably derive the estimate from a bounded percentile over multiple finalized blocks rather than the upper median of one block. [3](#0-2) [1](#0-0) 

For example, `make_signable_transaction` should reject `fee.0 > MAX_FEE_PER_VBYTE`, while `SignableTransaction::new` should independently enforce the bound and use `fee_per_vbyte.checked_mul(vbytes)` before performing affordability checks. [10](#0-9) [3](#0-2) 

### Proof of Concept

1. A public Bitcoin user broadcasts high-fee transactions so that, in the block selected as `block_number`, the transaction at index `fees.len() / 2` has rate `R`, for example `1_000_000` sat/vbyte.
2. `median_fee` computes each public transaction’s fee rate and returns `Fee(R)` without a maximum bound. [1](#0-0) 
3. A scheduled Bitcoin plan calls `prepare_send`, which calls `needed_fee`, which calls `make_signable_transaction(..., calculating_fee = true)`. [11](#0-10) [12](#0-11) 
4. `make_signable_transaction` passes `R` unchanged as `fee_per_vbyte`. [2](#0-1) 
5. If the generated transaction is `V = 200` vbytes, `SignableTransaction::new` calculates `needed_fee = 1_000_000 * 200 = 200_000_000` satoshis and accepts it whenever the inputs cover the payments plus that fee. [3](#0-2) 
6. The plan’s payments are reduced to amortize the calculated fee, and the subsequent `signable_transaction` call constructs the corresponding spend transaction with that fee schedule. [8](#0-7) [5](#0-4)

### Citations

**File:** processor/src/networks/bitcoin.rs (L387-415)
```rust
  // This function panics on a node which doesn't follow the Bitcoin protocol, which is deemed fine
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

**File:** processor/src/networks/bitcoin.rs (L803-815)
```rust
  async fn needed_fee(
    &self,
    block_number: usize,
    inputs: &[Output],
    payments: &[Payment<Self>],
    change: &Option<Address>,
  ) -> Result<Option<u64>, NetworkError> {
    Ok(
      self
        .make_signable_transaction(block_number, inputs, payments, change, true)
        .await?
        .map(|signable| signable.needed_fee()),
    )
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-235)
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

**File:** processor/src/networks/mod.rs (L442-545)
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

    // Amortize the fee over the plan's payments
    let (post_fee_branches, mut operating_costs) = (|| {
      // If we're creating a change output, letting us recoup coins, amortize the operating costs
      // as well
      let total_fee = tx_fee + if change.is_some() { operating_costs } else { 0 };

      let original_outputs = payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>();
      // If this isn't enough for the total fee, drop and move on
      if original_outputs < total_fee {
        let mut remaining_operating_costs = operating_costs;
        if change.is_some() {
          // Operating costs increase by the TX fee
          remaining_operating_costs += tx_fee;
          // Yet decrease by the payments we managed to drop
          remaining_operating_costs = remaining_operating_costs.saturating_sub(original_outputs);
        }
        return (drop_branches(key, &payments), remaining_operating_costs);
      }

      let initial_payment_amounts =
        payments.iter().map(|payment| payment.balance.amount.0).collect::<Vec<_>>();

      // Amortize the transaction fee across outputs
      let mut remaining_fee = total_fee;
      // Run as many times as needed until we can successfully subtract this fee
      while remaining_fee != 0 {
        // This shouldn't be a / by 0 as these payments have enough value to cover the fee
        let this_iter_fee = remaining_fee / u64::try_from(payments.len()).unwrap();
        let mut overage = remaining_fee % u64::try_from(payments.len()).unwrap();
        for payment in &mut payments {
          let this_payment_fee = this_iter_fee + overage;
          // Only subtract the overage once
          overage = 0;

          let subtractable = payment.balance.amount.0.min(this_payment_fee);
          remaining_fee -= subtractable;
          payment.balance.amount.0 -= subtractable;
        }
      }

      // If any payment is now below the dust threshold, set its value to 0 so it'll be dropped
      for payment in &mut payments {
        if payment.balance.amount.0 < Self::DUST {
          payment.balance.amount.0 = 0;
        }
      }

      // Note the branch outputs' new values
      let mut branch_outputs = vec![];
      for (initial_amount, payment) in initial_payment_amounts.into_iter().zip(&payments) {
        if Some(&payment.address) == Self::branch_address(key).as_ref() {
          branch_outputs.push(PostFeeBranch {
            expected: initial_amount,
            actual: if payment.balance.amount.0 == 0 {
              None
            } else {
              Some(payment.balance.amount.0)
            },
          });
        }
      }

      // Drop payments now worth 0
      payments = payments
        .drain(..)
        .filter(|payment| {
          if payment.balance.amount.0 != 0 {
            true
          } else {
            log::debug!("dropping dust payment from plan {}", hex::encode(plan_id));
            false
          }
        })
        .collect();

      // Sanity check the fee was successfully amortized
      let new_outputs = payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>();
      assert!((new_outputs + total_fee) <= original_outputs);

      (
        branch_outputs,
        if change.is_none() {
          // If the change is None, this had no effect on the operating costs
          operating_costs
        } else {
          // Since the change is some, and we successfully amortized, the operating costs were
          // recouped
          0
        },
```

**File:** processor/src/networks/mod.rs (L549-594)
```rust
    let Some(tx) = self
      .signable_transaction(
        block_number,
        &plan_id,
        key,
        &inputs,
        &payments,
        &change,
        &scheduler_addendum,
      )
      .await?
    else {
      panic!(
        "{}. {}: {}, {}: {:?}, {}: {:?}, {}: {:?}, {}: {}, {}: {:?}",
        "signable_transaction returned None for a TX we prior successfully calculated the fee for",
        "id",
        hex::encode(plan_id),
        "inputs",
        inputs,
        "post-amortization payments",
        payments,
        "change",
        change,
        "successfully amoritized fee",
        tx_fee,
        "scheduler's addendum",
        scheduler_addendum,
      )
    };

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
    }

    Ok(PreparedSend { tx: Some(tx), post_fee_branches, operating_costs })
```
