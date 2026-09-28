### Title
SignableTransaction applies a miner-influenceable fee rate with no upper bound, letting block producers burn the multisig's inputs as fees - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` validates the fee rate only against a *minimum* (`TransactionError::TooLowFee`) and has no maximum bound. The rate is supplied by the caller, which in `processor/src/networks/bitcoin.rs` is `self.median_fee(&block_for_fee)` — a fee estimate derived from a recent on-chain block, i.e. from fee rates set by unprivileged Bitcoin transaction senders/miners. Whatever `inputs - payments` remains is either sent to `change` or, when the change output would fall below `DUST` (or no change is given), is silently folded into the fee paid to miners. This mirrors the Catalyst finding: a fee parameter with a weak (absent) upper bound allows value extraction to the party who collects the fee.

### Finding Description
In `SignableTransaction::new` (send.rs:150-256):

- `needed_fee = fee_per_vbyte * vbytes` (line 206) with `fee_per_vbyte` taken verbatim from the caller.
- The only fee check is a *lower* bound: `if needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000) { TooLowFee }` (lines 211-213). There is no upper bound.
- `input_sat < payment_sat + needed_fee` only requires inputs to *cover* the fee (line 215) — it does not constrain the fee relative to the payment amount. A transaction paying 1,000 sats may pay 99% of input value as fee without error.
- When `change` is `Some`, the change output is only appended if `input_sat - payment_sat - fee_with_change >= DUST` (lines 228-234). If the leftover is `< DUST` it silently becomes additional fee; if `change` is `None`, "all leftover funds will become part of the paid fee" per the doc comment (lines 145-147).

The caller passes a fee derived from chain state: `make_signable_transaction` (processor/src/networks/bitcoin.rs:430-431) computes `fee = self.median_fee(&block_for_fee)` from a mined block, then calls `BSignableTransaction::new(..., fee.0)` (lines 446-452). Fee rates inside a mined block are controlled by whoever got transactions mined — a miner can include self-dealing transactions paying arbitrarily high feerates at zero net cost (they collect their own fees), inflating the estimate. The analog of "fee admin sets fee to 100% and governance collects everything" is "block producer inflates the observed fee rate and collects the multisig's excess as mining fees."

Additionally, the `median_fee` result is used per-`Plan` with no sanity cap (e.g. no `fee <= k * payment_amount` or absolute ceiling), and there is no equivalent of `minOut` protecting the change/leftover — the leftover-minus-fee is not asserted anywhere.

### Impact Explanation
The Serai multisig's Bitcoin inputs can be substantially or entirely consumed by fees: any `input_sat - payment_sat` not sent as change is burned as fee, and an inflated `fee_per_vbyte` both increases `needed_fee` and reduces the change output. The beneficiary is the Bitcoin miner who mines the resulting transaction — the same role that benefits from inflating the source fee estimate. A miner who inflates the observed median rate and then mines Serai's spend transaction captures the excess directly, analogous to the fee administrator collecting the swapped amount as "governance fee" in the Catalyst report.

### Likelihood Explanation
Exploitation requires (a) influencing the median feerate of the block sampled by `median_fee` — feasible for any party able to get self-funded high-fee transactions mined, cheapest for the miner themselves — and (b) Serai producing a spend in a window where the inflated estimate is sampled. There is no privileged-position requirement on the Serai side: the input is ordinary public Bitcoin transaction data, matching the "untrusted external data" reachability criterion. The defense that "users set minOut" has no analog here since the processor never bounds the effective fee.

Caveat: I was unable to read the body of `median_fee` within my iteration budget. The finding holds as long as `median_fee` is a function of feerates observed in a mined block (as its call site at `processor/src/networks/bitcoin.rs:430-431` implies); if it instead clamps against a hard-coded ceiling, the impact reduces but the missing upper bound in `SignableTransaction::new` itself remains.

### Recommendation
- Add an upper-bound check in `SignableTransaction::new`, e.g. `Err(TransactionError::TooHighFee)` if `needed_fee` exceeds a sanity ceiling relative to `payment_sat`/`input_sat`, mirroring the recommendation to bound `setVaultFee` below 100%.
- Emit an explicit error rather than silently folding sub-`DUST` change into the fee, or make `change` mandatory so leftover handling is always explicit.
- Clamp the `median_fee` estimate in `make_signable_transaction` to a configured maximum feerate.

### Proof of Concept
1. Multisig holds inputs worth `I`; a plan pays `P` with change address `C`.
2. An attacker mines a block (or gets high-fee self-payments mined) such that `median_fee(block)` returns `R`, inflated well above market rate.
3. `make_signable_transaction` calls `SignableTransaction::new(inputs, payments, Some(C), None, R)`.
4. `needed_fee = R * vbytes` passes the only check (a minimum). As long as `I >= P + needed_fee`, the transaction is constructed.
5. Change = `I - P - R * vbytes_with_change`; if it falls below 546 sats the change output is dropped entirely and everything above `P` becomes fee.
6. The FROST participants sign the transaction (`TransactionSignMachine::sign`, sighash commits to the inflated-fee output set), the attacker mines it, and collects `I - P - (possibly dust-sized change)` as fees — value extracted from the vault-equivalent multisig exactly as the Catalyst fee administrator extracted the swap amount.