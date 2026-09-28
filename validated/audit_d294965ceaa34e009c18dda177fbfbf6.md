### Title
Scanner reports dust outputs as spendable funds which cannot be economically spent — ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
The external report describes a bug class where a generic code path is applied to an asset/output type that requires special-case handling (native ETH passed through the ERC20 `safeApprove`/`transferFrom` path), and where the discriminator used to select the path is the wrong predicate (`msg.value > 0` instead of the token's identity). The analog in Serai's in-scope code is `Scanner::scan_transaction`, which classifies every output paying to a registered `script_pubkey` as a `ReceivedOutput` without discriminating on the output's type — specifically its value. Outputs below the dust threshold (`DUST = 546`, defined by Serai itself in `send.rs`) are reported as received funds even though spending them costs more in fees than they are worth, and Serai's own transaction builder refuses to *create* such outputs.

### Finding Description
`Scanner::scan_transaction` at `networks/bitcoin/src/wallet/mod.rs:199-214` matches outputs solely on `self.scripts.get(&output.script_pubkey)` and pushes a `ReceivedOutput` carrying `output.value` unconditionally. There is no check of `output.value.to_sat()` against any minimum.

Meanwhile, `networks/bitcoin/src/wallet/send.rs` demonstrates that the codebase already knows this class of output is special:

- `pub const DUST: u64 = 546;` (send.rs:32) with a comment citing Bitcoin Core policy.
- `SignableTransaction::new` rejects payments `< DUST` with `TransactionError::DustPayment` (send.rs:165-169).
- Change is only emitted if the leftover `value >= DUST` (send.rs:228-233).

So the send path discriminates on the value predicate, but the receive path does not — the exact same asymmetry as `setIncentive`/`storeTokens` discriminating on `msg.value` rather than the token type. A `ReceivedOutput` with, e.g., 100 sats enters the same pipeline as a normal output: it is summed into `input_sat` (send.rs:175), committed to via `Prevouts::All` in the Taproot sighash (send.rs:375-386), and signed. A key-path Taproot input costs roughly 58 vbytes; at any non-trivial `fee_per_vbyte`, a sub-546-sat input contributes less value than the marginal fee its inclusion adds (each input increases `vbytes` in `calculate_weight_vbytes`, send.rs:204-227), so including it strictly reduces the funds available for payments/change, or the transaction becomes nonstandard/uneconomical.

### Impact Explanation
This matches the accepted impact "funds reported received that are not spendable." An unprivileged party can send a transaction containing a dust-valued output to a Serai-registered `script_pubkey` (a miner can include such nonstandard outputs, and sub-dust Taproot outputs can also be created by parties willing to burn value). The scanner will report it as a received `ReceivedOutput`, inflating the wallet's apparent balance. Those funds are either unspendable in practice (value < marginal fee cost of the input) or actively harmful (including the input makes `SignableTransaction::new` consume the dust while raising `needed_fee`, potentially pushing an otherwise-fundable transaction into `NotEnoughFunds`). The scanner conflates two distinct output classes — economically spendable and dust — under one handling path, exactly mirroring the ETH-vs-ERC20 conflation in the reference report.

### Likelihood Explanation
Creating a sub-dust output requires miner cooperation or a sender willing to use nonstandard transactions, which lowers likelihood. However, the triggering input is entirely public (any Bitcoin transaction sent to the well-known Serai deposit script), requires no privileged position, and the misclassified output persists in the wallet's output set indefinitely, affecting every subsequent `SignableTransaction` that sweeps it.

### Recommendation
Mirror the fix pattern from the report — discriminate on the output's actual type (value relative to the dust limit) rather than treating all `script_pubkey` matches uniformly. Either:

1. In `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:205`), skip outputs where `output.value.to_sat() < DUST`, or
2. Record them distinctly so the scheduler can exclude them from `input_sat` accounting in `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:175`).

Option 1 is simplest and consistent with the codebase's own refusal to create dust.

### Proof of Concept
```rust
// An attacker (or a miner cooperating with one) crafts a transaction with:
//   output[0] = TxOut { value: 100 sats, script_pubkey: <serai p2tr script> }
//
// Scanner::scan_transaction returns:
//   [ ReceivedOutput { offset: ZERO, output: <100-sat TxOut>, outpoint } ]
//
// The coordinator/wallet records 100 sats as received. Later:
//   SignableTransaction::new(vec![dust_output, ...], payments, change, None, fee_rate)
// sums input_sat including the 100 sats (send.rs:175), while the extra input adds
// ~58 vbytes * fee_rate to needed_fee (send.rs:204-227) — more than 100 sats at any
// fee_rate >= 2 sat/vb — yielding a net-negative input, or NotEnoughFunds for a
// transaction that would succeed without it. The reported balance is unusable.
```
Uncertainty noted: whether the higher-level processor filters dust before scheduling could not be verified within the in-scope crates; within `networks/bitcoin` itself the receive/send asymmetry stands on its own.