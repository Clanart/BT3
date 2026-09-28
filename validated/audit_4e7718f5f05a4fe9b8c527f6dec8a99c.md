### Title
Scanner reports any output paying to a watched script as a spendable `ReceivedOutput`, letting an attacker dust the wallet with inputs that cost more to spend than they are worth - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The original bug is a missing lower bound on an attacker-influenced output (`minBuyAmount = 0`), letting a counterparty deliver a worthless result while consuming the input. The Serai analog is in `Scanner::scan_transaction` / `Scanner::scan_block`: matching is performed on `script_pubkey` alone with no minimum-value check, so any arbitrary party can create outputs paying to a Serai-registered script that are uneconomical (or immature) to spend, yet are reported by the wallet as spendable `ReceivedOutput`s.

### Finding Description
`Scanner::scan_transaction` iterates a transaction's outputs and, for any output whose `script_pubkey` appears in `self.scripts`, pushes a `ReceivedOutput` with the registered offset — without checking `output.value` at all (mod.rs:199-214). `scan_block` additionally scans `block.txdata[0]`, the coinbase transaction, whose outputs are immature for 100 blocks and therefore not spendable; the code only documents this in a comment ("a post-processing pass is needed") rather than enforcing it (mod.rs:216-227).

Two consequences follow:

1. **Dusting / economic drain.** An unprivileged party who observes a Serai deposit, change, or branch address can send tiny outputs (e.g., 546 sat, the relay dust limit) to it. `scan_transaction` reports these as normal received outputs. Spending a Taproot input costs ~57 vbytes (230 WU, per the in-file analysis in `send.rs`), so at moderate fee rates the input is worth less than the fee needed to spend it — a classic "funds reported received that are not spendable" condition, and if the wallet's fee/change math is forced to consume them it results in a net loss of funds.

2. **Immature coinbase outputs.** `scan_block` returns coinbase outputs to a registered script as immediately spendable `ReceivedOutput`s. Any transaction attempting to spend one is consensus-invalid, and any accounting treating it as available balance is wrong.

### Impact Explanation
An attacker with no privileges can degrade or drain wallet funds: dusted inputs are indistinguishable from legitimate receipts at the scanner layer, and spending them destroys more value than they carry (each Taproot input adds ~57 vbytes of fee obligation). Coinbase outputs reported before maturity make the wallet construct/sign transactions that are invalid on-chain, potentially stranding the remaining inputs' fees if ever broadcast. This maps directly to the report's class — a missing minimum/bound check on externally supplied value being treated as usable output.

### Likelihood Explanation
Sending a dust output to a known Serai address is trivially cheap for any Bitcoin user; deposit/change/branch scripts are observable on-chain once used. No cryptographic or validator compromise is required — only untrusted transaction data (which the scanner is designed to ingest from any transaction/block it is fed).

### Recommendation
- Enforce a minimum spendable threshold inside `Scanner::scan_transaction` — skip outputs whose `value` is below the cost of spending them (e.g., the `DUST`/economics constant already defined in `send.rs`/`Bitcoin::DUST`), so `ReceivedOutput` only ever represents economically spendable funds.
- In `scan_block`, skip `block.txdata[0]` (or enforce coinbase maturity at this layer) rather than relying on a caller-side post-processing pass documented only in a comment.

### Proof of Concept
Conceptual, based on `networks/bitcoin/src/wallet/mod.rs`:

```rust
// Attacker observes a Serai-registered script (e.g., a deposit address)
// and broadcasts a transaction paying 546 sats to it:
let dust_tx = Transaction {
    output: vec![TxOut {
        value: Amount::from_sat(546),            // relay-minimum dust
        script_pubkey: serai_watched_script,      // matches scanner.scripts
    }],
    ..Default::default()
};

// Scanner reports it as a normal spendable output — no value check:
let outputs = scanner.scan_transaction(&dust_tx);
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].value(), 546);

// Yet spending this input requires ~57 vbytes of witness+input weight;
// at any fee rate above ~9.6 sat/vb, the input is a net loss:
//   57 vbytes * 10 sat/vb = 570 sat fee > 546 sat input value.
// The wallet has recorded "funds received" that are not economically spendable.
```

Similarly, `scan_block` returns `scanner.scan_transaction(&block.txdata[0])` results as `ReceivedOutput`s even though coinbase outputs cannot be spent for 100 blocks, violating the spendability contract of `ReceivedOutput`.