### Title
Attacker-sent dust outputs are scanned as spendable and inflate the transaction's fee burden beyond their value, causing `SignableTransaction::new` to fail with `NotEnoughFunds` — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reported bug class is "the contract trusts an ambient balance that any unprivileged party can inflate, rather than the exact amount it controls, so an attacker's dust donation makes the critical call revert." The same shape exists in bitcoin-serai: `Scanner::scan_transaction` accepts any output paying to a registered script as a spendable `ReceivedOutput`, and `SignableTransaction::new` sums `input.output.value` into `input_sat` without checking whether each input is worth more than the fee needed to spend it. An unprivileged party can send a dust P2TR output (e.g., 1 sat) to the Serai vault address; once included, it adds ~57.5 vbytes of weight — costing `fee_per_vbyte * ~58` sats — while contributing 1 sat, which can push `input_sat < payment_sat + needed_fee` and make transaction construction fail.

### Finding Description
`Scanner::scan_transaction` in `networks/bitcoin/src/wallet/mod.rs` (lines 199–214) emits a `ReceivedOutput` for every output whose `script_pubkey` matches a registered script. There is no minimum-value check, so a dust output is indistinguishable from a real deposit. In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`):

- All inputs' values are summed at line 175: `input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum()`.
- Each input contributes a fixed `TxIn` to `calculate_weight_vbytes` (lines 68–84), so each input raises `vbytes` by ~58 vbytes.
- `needed_fee = fee_per_vbyte * vbytes` (line 206) and the balance check `input_sat < payment_sat + needed_fee` → `NotEnoughFunds` (lines 215–221).

A dust input whose `value < fee_per_vbyte * ~58` is net-negative: it reduces the spendable amount available for payments. Unlike the DEX pallet — where `add_liquidity` computes reserves from live balances but the test `add_tiny_liquidity_directly_to_pool_address` shows donated coins don't corrupt accounting — here the "donated" dust is itself consumed as an input, mirroring how `balanceOf(address(this))` in the reference bug silently included Bob's transfer.

### Impact Explanation
An unprivileged party sends a small Bitcoin transaction (explicitly in-scope: "Bitcoin transactions they send") paying dust to the Serai vault's external/branch/change address. The scanner reports it as a received output. When the signing set builds a `SignableTransaction` over the full UTXO set, each dust input either:

- Causes `NotEnoughFunds` where the transaction would otherwise succeed (denial of service on withdrawals/relays), or
- If the transaction still succeeds, burns `fee_per_vbyte * ~58 - dust_value` sats of vault funds as fee per dust input.

This is "funds reported received that are not profitably spendable" and a reachable abort of the signing flow. Severity: Medium — the attack is cheap to repeat, but an integrator could filter inputs client-side; nothing in the in-scope API warns about or prevents it.

### Likelihood Explanation
Sending a dust P2TR output to a known Serai address is trivially reachable by anyone. Whether it triggers depends on the coordinator including all scanned outputs as inputs, which is the natural usage pattern (`SignableTransaction::new` takes a `Vec<ReceivedOutput>` with no guidance to prune dust). `multisig()` will even produce valid signatures for the dust input, so nothing downstream detects it.

### Recommendation
In `SignableTransaction::new`, drop or reject inputs whose `value` is less than the marginal fee of adding one input (`fee_per_vbyte` × per-input vbytes from `calculate_weight_vbytes(1, &[], None)`), and/or enforce a `DUST` minimum on `ReceivedOutput` values in `Scanner::scan_transaction` so sub-economical outputs are never reported as deposits.

### Proof of Concept
```rust
// networks/bitcoin/tests/wallet.rs style, regtest
let (keys, key) = keys();
let scanner = Scanner::new(key).unwrap();

// Attacker sends a 1-sat P2TR output to the vault key (anyone can do this).
let dust = /* tx paying 1 sat to p2tr_script_buf(key) */;
let dust_output = scanner.scan_transaction(&dust)[0].clone(); // accepted, no min check

// Legitimate deposit of exactly payment + bare fee
let real = send_and_get_output(&rpc, &scanner, key).await; // e.g. 100_000 sats

// Victim tries to spend all scanned outputs
let payments = [(p2tr_script_buf(key).unwrap(), 99_000)];
// Without dust: input 100_000 >= 99_000 + fee(1 input) -> succeeds.
// With dust input: adds ~58 vb * FEE sats of needed_fee for +1 sat of input_sat,
// so input_sat < payment_sat + needed_fee -> NotEnoughFunds.
let res = SignableTransaction::new(vec![real, dust_output], &payments, None, None, FEE);
assert!(matches!(res, Err(TransactionError::NotEnoughFunds { .. })));
```