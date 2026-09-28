### Title
Unbounded `fee_per_vbyte` lets an attacker inflate the multisig's transaction fee via attacker-influenced `median_fee` - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The sandwich-attack bug class — "the signer accepts whatever price the environment dictates because no bound is enforced" — maps onto `SignableTransaction::new`'s `fee_per_vbyte` parameter in `bitcoin-serai`. The processor derives this rate from `Bitcoin::median_fee`, which computes the median fee-rate of all non-coinbase transactions in the tip block — data any unprivileged party can influence by simply broadcasting a Bitcoin transaction. `SignableTransaction::new` enforces only a *lower* bound (`TooLowFee`); there is no upper bound on the fee it will commit to the transaction. The resulting `SignableTransaction` is passed to the threshold signers, who sign it without any independent cap, so an arbitrarily inflated fee rate is paid out of the multisig's inputs.

### Finding Description
`Bitcoin::median_fee` in `processor/src/networks/bitcoin.rs` builds its fee estimate from the transactions included in the latest block:

```rust
// processor/src/networks/bitcoin.rs:388-415
async fn median_fee(&self, block: &Block) -> Result<Fee, NetworkError> {
  let mut fees = vec![];
  if block.txdata.len() > 1 {
    for tx in &block.txdata[1 ..] {
      ...
      fees.push((in_value - out) / u64::try_from(tx.vsize()).unwrap());
    }
  }
  fees.sort();
  let fee = fees.get(fees.len() / 2).copied().unwrap_or(0);
  Ok(Fee(fee.max(1)))
}
```

Every fee-rate sample comes from a transaction a third party constructed. `make_signable_transaction` feeds this directly into `BSignableTransaction::new` (`processor/src/networks/bitcoin.rs:430-451`). In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:206-213`), the only check is:

```rust
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
```

There is no symmetric `TooHighFee`. If `input_sat >= payment_sat + needed_fee` holds, the transaction is built paying `fee_per_vbyte * vbytes`, and any residual beyond the change output is also burned as fee. `prepare_send` (`processor/src/networks/mod.rs:442-495`) amortizes whatever fee is reported across payments, dropping them below dust if needed, rather than rejecting an unreasonable rate. The threshold signing flow then signs this transaction as-is — the signers never see or bound the rate, only the sighash.

### Impact Explanation
The multisig's withdrawal/branch transactions can be made to pay a fee orders of magnitude above market rate, bounded only by `input_sat - payment_sat`. In blocks with few transactions (e.g., a block containing just the coinbase plus the attacker's transaction), `fees` has a single element and the attacker's transaction *is* the median — full control. Even in fuller blocks, an attacker controlling half the non-coinbase transactions controls the median. A miner can include a self-paying transaction at effectively zero cost (it collects its own fee), making the manipulation free while the inflated fee is paid by Serai to whoever mines the withdrawal — a direct value-extraction path structurally identical to the MEV sandwich: the protocol accepts whatever "price" the attacker set.

### Likelihood Explanation
Any unprivileged party can submit a Bitcoin transaction with an arbitrary fee rate; getting it mined requires paying the fee once (or nothing for a miner). Fee estimation uses only the single tip block (`block_for_fee = self.get_block(block_number)`), so a freshly mined block under attacker influence immediately steers the next plan's fee. The attack drains value as fees rather than stealing outputs directly, which is why severity is Medium rather than High — the attacker profits mainly when they (or a cooperating miner) mine the withdrawal transaction, and the loss is capped by the inputs selected.

### Recommendation
Cap `fee_per_vbyte` in `SignableTransaction::new` (analogous to the recommended oracle-derived price band), e.g. reject rates above `k * DEFAULT_MIN_RELAY_TX_FEE` or a caller-specified `max_fee`. Robustify `median_fee` by sampling multiple historical blocks and/or clamping the result to a sane range, so a single attacker-influenced block cannot set the rate.

### Proof of Concept
1. An attacker broadcasts a transaction paying a fee rate of, e.g., 10,000 sat/vbyte (cost borne once; free if self-mined).
2. The block confirming it contains few transactions such that the attacker's rate is the median.
3. The Serai processor calls `make_signable_transaction` for the next `Plan`; `median_fee` returns ~10,000 sat/vbyte.
4. `SignableTransaction::new` succeeds (no upper bound); the change output shrinks or payments are amortized/dropped to cover the fee.
5. The threshold signs and broadcasts a transaction paying `fee_per_vbyte * vbytes`, burning the multisig's funds to miners — the attacker-profit analog of the sandwich extraction.