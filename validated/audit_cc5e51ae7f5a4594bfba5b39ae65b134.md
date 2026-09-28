### Title
Unbounded attacker-influenced fee rate allows burning multisig funds as miner fees (no slippage cap) - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::median_fee` derives the fee rate used for all Serai multisig spends from the *median fee rate of transactions in a single, attacker-controllable block*, and `SignableTransaction::new` enforces only a *minimum* fee (`TooLowFee`) with no upper bound. The external report's bug class — a swap/spend executed with no slippage/limit check so an unprivileged party can manipulate the price paid — maps directly onto Serai's fee pipeline: an attacker who places high-fee transactions in the block sampled for the fee estimate can force the threshold wallet to pay an arbitrarily inflated fee, which is amortized out of user payments or burnt entirely.

### Finding Description
`make_signable_transaction` picks `block_for_fee = get_block(block_number)` and uses `median_fee(&block_for_fee)` as `fee_per_vbyte` for the transaction the FROST multisig will sign [1](#0-0) . `median_fee` computes `(in_value - out) / vsize` for each non-coinbase transaction and takes the middle element, clamped only below at 1 sat/vbyte [2](#0-1) .

`SignableTransaction::new` then computes `needed_fee = fee_per_vbyte * vbytes` and rejects only if the fee is *below* the relay minimum — there is no sanity cap on how high the fee may be [3](#0-2) . In `prepare_send`, this fee is amortized across user payments (`payment.balance.amount.0 -= subtractable`), dropping any that fall below dust [4](#0-3) .

Because Bitcoin blocks are public and any party can broadcast transactions, the sampled median is attacker-influenceable: in a block containing few transactions (common on low-congestion periods), flooding it with self-paying high-fee transactions shifts the median arbitrarily upward. Unlike the wagmi case where a sandwich steals via a DEX price, here the "price" is sats/vbyte and the victim is the threshold wallet's fee budget. An attacker who is also a miner (or who simply wants to grief) recovers or externalizes their own cost while Serai overpays `inflated_rate × tx_vsize` to the miner of its transaction.

### Impact Explanation
Loss of funds: the multisig signs a transaction whose fee is inflated far above market rate; the fee is deducted from user burn/withdrawal payments (or entire payments are dropped below dust). With a 520-input transaction (`MAX_INPUTS`/`MAX_OUTPUTS` bound the size to ~400k weight units [5](#0-4) ), even a modest median inflation multiplies across a large vsize. The signed fee difference is irrecoverable — it goes to whichever miner confirms the transaction.

### Likelihood Explanation
Medium. The attacker only needs to influence the median of one specific block — the one sampled as `block_for_fee`. Blocks frequently contain few transactions; when `txdata.len() - 1` is small, a handful of self-funded high-fee transactions controls the median. The attack requires no compromised validator, RPC, or peer — only the ability to send public Bitcoin transactions, matching the unprivileged-attacker reachability requirement. The cost to the attacker is bounded by the fees they pay on their own (typically small) transactions, while the damage scales with Serai's transaction size.

### Recommendation
Add an upper bound (slippage cap) on the accepted fee rate, analogous to enforcing `minAmountOut`:
- Cap `median_fee` at a multiple of a long-run/reference fee (e.g., max over several recent blocks or a configured ceiling), per the existing `TODO2` noting a cached multi-block fee should be used [6](#0-5) .
- Add a `TooHighFee` variant to `TransactionError` and reject `fee_per_vbyte` above a protocol-defined maximum in `SignableTransaction::new`, alongside the existing `TooLowFee` check [7](#0-6) .

### Proof of Concept
1. Attacker monitors for a Bitcoin block that will be used as `block_for_fee` and observes it has few transactions.
2. Attacker broadcasts `k` self-paying transactions with e.g. 10,000 sat/vbyte so that they constitute the median element in `block.txdata[1..]` [8](#0-7) .
3. `make_signable_transaction` returns `fee.0 = 10_000`; `SignableTransaction::new` accepts it since only `TooLowFee` is checked [9](#0-8) .
4. `prepare_send` amortizes `tx_fee = 10_000 * vbytes` across payments, shrinking or dropping them [10](#0-9) .
5. The threshold signs and broadcasts a transaction paying ~1000× market fee; the excess is irreversibly burnt to miners, funded by user balances.

### Citations

**File:** processor/src/networks/bitcoin.rs (L388-414)
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
```

**File:** processor/src/networks/bitcoin.rs (L429-453)
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
      Ok(signable) => Ok(Some(signable)),
```

**File:** processor/src/networks/bitcoin.rs (L564-576)
```rust
// Bitcoin has a max weight of 400,000 (MAX_STANDARD_TX_WEIGHT)
// A non-SegWit TX will have 4 weight units per byte, leaving a max size of 100,000 bytes
// While our inputs are entirely SegWit, such fine tuning is not necessary and could create
// issues in the future (if the size decreases or we misevaluate it)
// It also offers a minimal amount of benefit when we are able to logarithmically accumulate
// inputs
// For 128-byte inputs (36-byte output specification, 64-byte signature, whatever overhead) and
// 64-byte outputs (40-byte script, 8-byte amount, whatever overhead), they together take up 192
// bytes
// 100,000 / 192 = 520
// 520 * 192 leaves 160 bytes of overhead for the transaction structure itself
const MAX_INPUTS: usize = 520;
const MAX_OUTPUTS: usize = 520;
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

**File:** processor/src/networks/mod.rs (L480-502)
```rust
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
```
