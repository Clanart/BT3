### Title
Dust outputs donated to the multisig are counted as fully spendable balance, inflating `input_sat` while costing more in fee weight than their value - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reported bug class is optimistic accounting: the contract aggregates balances and commits to consuming them as if fully usable, without checking that each unit is actually worth what it contributes, producing an overestimate that breaks the operation and is triggerable by an unprivileged party donating dust. The same shape exists in `bitcoin-serai`'s transaction builder. `Scanner::scan_transaction` accepts every output paying to a registered `script_pubkey` as a `ReceivedOutput` regardless of value, and `SignableTransaction::new` sums all input values into `input_sat` and treats the total as available to cover payments plus fee, while each input unconditionally contributes ~57 vbytes of signature weight to the fee. There is no per-input check that an input's value exceeds the marginal fee required to spend it, unlike the explicit `DustPayment` check applied to payments.

### Finding Description
`Scanner::scan_transaction` matches solely on `output.script_pubkey` and emits a `ReceivedOutput` for any value, including 1-sat outputs (`networks/bitcoin/src/wallet/mod.rs:199-214`). In `SignableTransaction::new`, inputs are only required to be non-empty (`send.rs:157-159`); the dust check at `send.rs:165-169` applies only to `payments`. The aggregate balance check `input_sat < payment_sat + needed_fee` at `send.rs:215-221` treats every donated dust satoshi as spendable liquidity, yet `calculate_weight_vbytes` correctly charges each input a fixed 64-byte-witness Taproot input weight (`send.rs:71-84`). The result: each sub-marginal-fee input nets *negative* value — it contributes `value` sats but costs `57 * fee_per_vbyte` sats — so the wallet's effective spendable balance is overestimated exactly as Burve overestimated mintable liquidity, with the deficit paid by the wallet's real funds as miner fees.

### Impact Explanation
An attacker who learns a multisig's P2TR `script_pubkey` (public, derivable from the group key or observed on-chain) can send dust outputs to it. Each such output is scanned and included in transaction construction; at nonzero fee rates the cost of spending it exceeds its value, so the multisig overpays fees on every transaction that consumes these inputs. At a 50 sat/vB rate a ~330-sat input costs ~2850 sats to spend — a >7x griefing multiplier on the attacker's cost — and up to `MAX_INPUTS = 520` such inputs can poison the input set (`processor/src/networks/bitcoin.rs:575`). Unlike Burve's revert, Bitcoin has no revert; the damage manifests as silent fee drain and, in the extreme, transactions whose aggregate check still passes while the wallet loses net funds, plus blocked scheduling when worthless inputs crowd the selection.

### Likelihood Explanation
The attack requires only sending ordinary Bitcoin transactions — a public input explicitly in scope — with no validator cooperation, and is repeatable for as long as the script_pubkey is reused for deposits. It does require the input-selection layer to actually consume the dust inputs, so impact depends on scheduler ordering; the wallet layer itself places no barrier.

### Recommendation
Filter `ReceivedOutput`s below a spendability threshold at scan time (compare `output.value` against `MARGINAL_INPUT_VBYTES * fee_per_vbyte` or a fixed conservative bound analogous to the documented `DUST` rationale at `processor/src/networks/bitcoin.rs:606-637`), or reject inputs in `SignableTransaction::new` whose value is less than the marginal fee their weight adds.

### Proof of Concept
1. Compute `p2tr_script_buf(group_key)` for a target multisig (from `Scanner::new`, `mod.rs:162-166`).
2. Broadcast transactions creating 330–546 sat outputs to that script — accepted by `scan_transaction` since only `script_pubkey` is matched (`mod.rs:205`).
3. When the wallet builds a transaction including these inputs, `input_sat` at `send.rs:175` counts the dust while `calculate_weight_vbytes` at `send.rs:204` bills each input ~57 vB; the resulting `fee()` (`send.rs:138-141`) exceeds the sum of input values contributed by the dust, draining the honest inputs' change.

Caveat: I could not fully trace the input-selection policy in `processor/src/multisigs/scheduler/utxo.rs` to confirm dust inputs are preferentially consumed; the wallet-layer accounting flaw itself is confirmed by `send.rs` and `mod.rs`.