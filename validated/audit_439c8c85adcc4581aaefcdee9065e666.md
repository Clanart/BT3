### Title
Dust/Unspendable UTXO Griefing — `Scanner` Credits Outputs That Cannot Be Economically Spent - (networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to a receiver who can grief a push-payment refund by reverting, any unprivileged party can send dust outputs to a scanned Serai Bitcoin key. `Scanner::scan_transaction` reports every output paying to a registered script regardless of value, and `SignableTransaction::new` never checks whether an input's value exceeds the marginal fee required to spend it. These outputs are reported as received funds yet can never be net-recovered, and accumulating them degrades or blocks legitimate spends.

### Finding Description
`Scanner::scan_transaction` matches only `output.script_pubkey` against registered scripts and returns a `ReceivedOutput` with no value floor (`mod.rs` lines 199–214). In `SignableTransaction::new` (`send.rs`), each input adds ~68 vbytes of weight via `calculate_weight_vbytes` (lines 71–84), and the fee is computed as `fee_per_vbyte * vbytes` (line 206). An input worth less than its marginal fee makes `input_sat < payment_sat + needed_fee` more likely (line 215), forcing `NotEnoughFunds`, and enough dust inputs push `weight` past `MAX_STANDARD_TX_WEIGHT` (line 241), returning `TooLargeTransaction`. The only dust check applies to *payments* (line 166), never to inputs or the reported outputs. Since the P2TR script for any offset is publicly derivable (`key + offset*G`), an attacker needs no secret to target the wallet.

### Impact Explanation
Funds are reported received but are economically unspendable: the threshold wallet's view of balance includes dust UTXOs whose recovery costs more in fees than their value. Worse, a dust flood can drive transaction weight over the standardness limit or starve `input_sat`, causing all transaction construction to fail — a griefing/blackmail vector mirroring the original finding where one party blocks settlement for everyone.

### Likelihood Explanation
Sending dust to a P2TR address requires only a normal Bitcoin transaction; no threshold participation, collusion, or privileged access is needed. Dust-spraying known deposit addresses is a well-established, low-cost attack.

### Recommendation
Have `Scanner::scan_transaction` (or a documented post-processing step, like the coinbase-maturity note at lines 218–220) filter outputs below a minimum spendable threshold — e.g., `DUST` or value ≥ marginal input fee — and have `SignableTransaction::new` reject inputs whose value is less than the fee they add.

### Proof of Concept
```rust
// Attacker learns the vault script (public: p2tr_script_buf(key)).
// They broadcast N transactions each paying `DUST - 1` sats to it.
let dust_tx = transaction_with_output(script.clone(), DUST - 1);
let received = scanner.scan_transaction(&dust_tx);
assert_eq!(received.len(), 1);           // credited as funds
// Every ReceivedOutput is indistinguishable from a spendable one.
// In SignableTransaction::new, each input adds ~68 vbytes of witness+input
// weight; at fee_per_vbyte = 10, a 545-sat input nets ~545 - 680 < 0 sats.
// With enough dust inputs:
//   - input_sat < payment_sat + needed_fee  -> NotEnoughFunds
//   - weight > MAX_STANDARD_TX_WEIGHT       -> TooLargeTransaction
// Legitimate withdrawals are blocked until operators manually filter UTXOs
// outside the library, and the dust value is never recoverable.
```