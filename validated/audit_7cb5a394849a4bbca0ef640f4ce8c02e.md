### Title
`SignableTransaction::new` lacks any upper bound on the fee rate, letting an externally-influenced fee oracle burn up to the entire change value as miner fees - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to `VaderPoolV2.mintFungible` minting liquidity units against attacker-manipulable reserves with no user-specified minimum, `SignableTransaction::new` computes `needed_fee = fee_per_vbyte * vbytes` and accepts the caller-supplied `fee_per_vbyte` with no maximum bound and no minimum-change guarantee. The only caller, `Bitcoin::make_signable_transaction`, sources `fee_per_vbyte` from `median_fee(block)`, which is computed purely from the fee rates of transactions in a recent block — transactions any unprivileged party can broadcast. An attacker who inflates the median fee rate causes the threshold wallet to sign a transaction paying an arbitrarily inflated fee, capped only by `sum(inputs) - sum(payments)`.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` derives the fee as `fee_per_vbyte * vbytes` and checks only a lower bound (`TooLowFee`) against `DEFAULT_MIN_RELAY_TX_FEE` — there is no upper bound or sanity cap:

```rust
// networks/bitcoin/src/wallet/send.rs:206-213
let mut needed_fee = fee_per_vbyte * vbytes;
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
```

The only constraint on the total fee is solvency:

```rust
// networks/bitcoin/src/wallet/send.rs:215-221
if input_sat < (payment_sat + needed_fee) {
  Err(TransactionError::NotEnoughFunds { .. })?;
}
```

When `change` is `Some`, the code silently drops the change output if the remainder under the *inflated* `fee_with_change` is below `DUST` or underflows, so `needed_fee` itself consumes the excess (lines 224–235). When `change` is `None`, `input_sat - payment_sat` becomes the fee in its entirety (documented at lines 145–147), so the signed transaction can burn nearly all input value as fees. The actual fee paid is `sum(inputs) - sum(outputs)` (lines 137–141), and the signatures commit to this via `Prevouts::All` sighashes (lines 373–390), so the burn is finalized once shares combine.

The `fee_per_vbyte` value is not chosen by the signer; `processor/src/networks/bitcoin.rs:417-452` sets it to `self.median_fee(&block_for_fee)`, and `median_fee` (lines 388–415) takes the median per-vbyte fee of all non-coinbase transactions in a single recent block — data entirely determined by transactions broadcast to the public Bitcoin mempool, i.e., public attacker-controlled inputs.

### Impact Explanation
The FROST threshold key signs a transaction committing to `Prevouts::All` amounts and output values, so once shares are combined the oversized fee is irreversibly paid to miners. Up to `sum(inputs) - sum(payments)` satoshis of Serai wallet funds are converted to miner fees instead of change. This is the same value-extraction shape as the reference report: the party committing funds has no way to bound the "price" (fee rate) at execution time, and the bound is derived from a manipulable on-chain value oracle (block fee market ↔ AMM reserves). A miner who mines a block packed with their own high-fee transactions both inflates the median and collects a portion of the inflated fee back, subsidizing the attack.

### Likelihood Explanation
Exploitation requires skewing the median fee of a block (more than half of the non-coinbase transactions must pay the inflated rate) within the window between `needed_fee` estimation and transaction creation. This requires paying real fees on many transactions or mining a block, so it is expensive and only profitable when the wallet's change value is large — matching the medium-severity, conditional-profitability profile of the merged slippage reports. The mechanic itself is deterministic: `SignableTransaction` will accept any `fee_per_vbyte` that passes `NotEnoughFunds`.

### Recommendation
Add a caller-specified `max_fee` (or `min_change`) bound to `SignableTransaction::new`, erroring if `needed_fee` exceeds it — the direct analog of adding a minimum-LP-token parameter. Additionally, `median_fee` should aggregate over several blocks (per the existing `TODO2`) and/or clamp to a sane ceiling to reduce oracle manipulability, and a dropped-below-dust change output should be treated as an error rather than silently absorbed into the fee when `change` was explicitly requested.

### Proof of Concept
1. Wallet holds an input worth `input_sat`; a plan requests `payment_sat` of payments with a `change` address (or none).
2. Attacker broadcasts a block's worth of transactions paying an inflated `f` sat/vbyte such that the median fee of the next block equals `f` (cheapest if the attacker mines the block).
3. `make_signable_transaction` reads `median_fee = f` and calls `SignableTransaction::new` with `fee_per_vbyte = f`.
4. `needed_fee = f * vbytes` passes all checks as long as `input_sat >= payment_sat + needed_fee`. If the change remainder falls below `DUST` (or underflows `checked_sub` at line 228), the change output is dropped and `f * vbytes` — up to nearly `input_sat - payment_sat` — is paid as fees.
5. Threshold signing produces a valid transaction (sighash commits to the stated prevouts/outputs); the excess is paid to miners and is unrecoverable.

Uncertainty note: `processor/` is outside the declared in-scope crates, so the `median_fee` call chain is cited as the reachable public-input source; the in-scope defect itself is the missing bound in `networks/bitcoin/src/wallet/send.rs`. If the scope is interpreted as excluding the fee-source reachability argument, the in-crate issue reduces to an unchecked-parameter concern of lower severity.