### Title
Missing upper bound on `fee_per_vbyte` allows arbitrary fee inflation / unchecked `u64` fee arithmetic - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` enforces only a *minimum* fee (`TooLowFee`) and has no maximum cap on the caller-supplied `fee_per_vbyte`. Analogous to the missing `maxLicenseFee`, the fee is a consensus/economic parameter bounded only from below: `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) and `fee_with_change = fee_per_vbyte * vbytes_with_change` (send.rs:227) are unchecked `u64` multiplications, and `input_sat < payment_sat + needed_fee` (send.rs:215) is an unchecked addition that can wrap in release builds.

### Finding Description
The only sanity check on `fee_per_vbyte` is the floor at send.rs:211 (`DEFAULT_MIN_RELAY_TX_FEE`). There is no ceiling. The value is derived from untrusted on-chain data — the median fee of transactions in a sampled block (`median_fee` in `processor/src/networks/bitcoin.rs:388-415` does `fees.push((in_value - out) / vsize)` for every non-coinbase tx and applies only `.max(1)` as a floor). An unprivileged party who sends Bitcoin transactions can therefore influence `fee_per_vbyte` upward without limit: if attacker transactions constitute the median position of the sampled block, they set the fee rate arbitrarily.

Two concrete consequences in the in-scope code:

1. **Fee inflation**: an inflated `fee_per_vbyte` inflates `needed_fee`/`fee_with_change`, shrinking the change output (send.rs:228-234). As long as `input_sat >= payment_sat + needed_fee`, the transaction is built and the multisig signs a sighash committing to the oversized fee — funds are burned to miners with no cap.

2. **Unchecked arithmetic**: `fee_per_vbyte * vbytes` can overflow `u64` (panic in debug, wrap in release); `payment_sat + needed_fee` can wrap to a small value, defeating the `NotEnoughFunds` check at send.rs:215 entirely, so a transaction with an absurd intended fee proceeds to construction.

### Impact Explanation
A transaction is produced whose sighash commits to an excessive (or wrapped) fee, and the threshold signature makes it final and irreversible. Loss is bounded only by total input value; an attacker who is also a miner captures the fee directly. Alternatively the overflow panic is a signer liveness failure.

### Likelihood Explanation
Medium. The attacker must place enough high-fee transactions to control the median of the specific block sampled by `median_fee`, and the inflated fee must still be covered by the multisig's inputs (otherwise `NotEnoughFunds`/`NoOutputs` aborts). It requires no privileged access, no collusion among validators, and only public Bitcoin transactions — matching the reachable-input rules.

### Recommendation
Define a `max_fee_per_vbyte` bound in `SignableTransaction::new` (and reject `fee_per_vbyte` above it), and use `checked_mul`/`checked_add` for `needed_fee`, `fee_with_change`, and `payment_sat + needed_fee`, returning an error on overflow instead of panicking or wrapping.

### Proof of Concept
- `networks/bitcoin/src/wallet/send.rs:206` — `let mut needed_fee = fee_per_vbyte * vbytes;` (unchecked multiply, no upper bound on `fee_per_vbyte`).
- `networks/bitcoin/src/wallet/send.rs:211-213` — only a minimum-fee check (`TooLowFee`); no maximum.
- `networks/bitcoin/src/wallet/send.rs:215` — `input_sat < (payment_sat + needed_fee)` uses wrapping-prone addition.
- `networks/bitcoin/src/wallet/send.rs:227-234` — `fee_with_change` reduces the change output; inflated fee transfers value from change to miners.
- `processor/src/networks/bitcoin.rs:388-415` — `median_fee` computes `fee_per_vbyte` from `(in_value - out) / vsize` of every non-coinbase transaction in a block (attacker-sendable inputs) with only `.max(1)` as a floor and no ceiling.