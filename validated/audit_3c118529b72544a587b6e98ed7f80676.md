### Title
Attacker can send dust outputs to a Serai deposit address which are accepted as spendable inputs yet cost more fee to spend than their value - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

Analogous to the tBTC issue where a funder's payment is consumed even though the keep never becomes usable, an unprivileged party can send a dust-value Bitcoin output to a known Serai deposit script. `Scanner::scan_transaction` accepts any output paying to a registered `script_pubkey` with no minimum-value check, returning it as a `ReceivedOutput`. `SignableTransaction::new` then accepts such outputs as inputs unconditionally. Because every Taproot input adds ~57 vbytes of weight (and hence fee), a dust input (e.g., 1–500 sats) contributes less value than the fee required to spend it, making the reported funds economically unspendable and a net loss when included.

### Finding Description

`Scanner::scan_transaction` iterates transaction outputs and matches only on `script_pubkey` membership in `self.scripts`, pushing a `ReceivedOutput` for every match regardless of `output.value` (`networks/bitcoin/src/wallet/mod.rs:199-214`). There is no check that the value exceeds the cost of spending the output.

On the spend side, `SignableTransaction::new` sums all supplied inputs (`input_sat`) and requires only `input_sat >= payment_sat + needed_fee` (`networks/bitcoin/src/wallet/send.rs:175-221`). The fee model in `calculate_weight_vbytes` charges each input a 64-byte witness signature plus fixed fields (`networks/bitcoin/src/wallet/send.rs:71-84`), ~57 vbytes per the in-code analysis (`processor/src/networks/bitcoin.rs:606-620`). So each attacker-supplied dust input adds fee cost without meaningful value.

### Impact Explanation

Any Bitcoin sent to a Serai address is "received" per `scan_transaction`, yet outputs below the marginal fee cost cannot be spent profitably. Attackers can flood a publicly known Serai address with dust outputs; the wallet either accumulates permanently unspendable UTXOs (funds reported received that are not spendable) or, when they are used as inputs, burns more in fees than the inputs are worth. This mirrors the original report's core loss: value is committed to the system while the corresponding resource never becomes usable.

### Likelihood Explanation

Likelihood is high in the sense that the attack requires only sending a standard Bitcoin transaction to a publicly known script — no validator status, no collusion, no special access. Impact per output is bounded by the marginal input fee plus dust amount, so severity is Medium rather than High.

### Recommendation

In `Scanner::scan_transaction`, skip (or flag as uneconomical) outputs whose value is below a spendability threshold derived from the marginal per-input vbytes at the target fee rate — e.g., reuse the `DUST`/economic-spendability reasoning already documented in `processor/src/networks/bitcoin.rs:622-638`. Alternatively, have `SignableTransaction::new` drop inputs whose value is less than the fee they induce.

### Proof of Concept

1. Obtain a Serai deposit `script_pubkey` (publicly visible on-chain).
2. Send a transaction with an output `{ value: 100, script_pubkey: serai_script }`.
3. `scanner.scan_transaction(&tx)` returns a `ReceivedOutput` with `value() == 100` (`mod.rs:199-214`).
4. `SignableTransaction::new(vec![dust_output], &payments, ...)` accepts it; the input's ~57 vbytes at any sane fee rate costs more than 100 sats, so spending it is a net loss, and the wallet records funds it cannot economically spend.