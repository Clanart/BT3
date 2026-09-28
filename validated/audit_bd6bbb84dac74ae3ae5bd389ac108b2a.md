### Title
`Scanner::scan_transaction` reports dust/economically-unspendable outputs as spendable `ReceivedOutput`s, forcing fee burn or stuck payments - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes a function documented to "distribute all" of a balance which silently leaves a remainder it cannot place. The Serai analog is `Scanner::scan_transaction` in `networks/bitcoin/src/wallet/mod.rs` (lines 199-214), which claims to return the spendable outputs received to the vault, yet returns every output whose `script_pubkey` matches a registered script with no value/dust/maturity check. The rest of the pipeline (`SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` lines 150-256, and the UTXO scheduler aggregation which drains all UTXOs into plans) then treats each `ReceivedOutput` as spendable balance.

### Finding Description
`scan_transaction` iterates `tx.output` and pushes a `ReceivedOutput` for any output whose `script_pubkey` exists in `self.scripts` — matching purely on the script, never on `output.value`. There is no filter against `DUST` (defined as 546 in `send.rs:32`), no check that the output's value exceeds the fee cost of spending it (~58+ vbytes per Taproot input at the prevailing fee rate), and `scan_block` (lines 221-227) additionally includes coinbase outputs which are not spendable for 100 blocks (only a doc comment warns about this; nothing enforces it).

Downstream, `SignableTransaction::new` unconditionally sums every supplied input into `input_sat` (line 175) and adds each as a `TxIn`, so a dust input's contribution is net-negative: it adds weight and fee cost greater than its value. When the scheduler aggregates all wallet UTXOs (change/aggregation plans drain every chunk), these dust inputs are forced into transactions, burning vault funds on fees — or, when instructed payments consume nearly the whole balance, causing `NotEnoughFunds` failures the operator cannot resolve, since the vault cannot refuse to carry the dust output.

### Impact Explanation
An unprivileged party who knows a vault script_pubkey (public on-chain once used) can send transactions containing arbitrarily many tiny outputs to it. Each is reported by the scanner/scan RPC as a received output and enters the spendable UTXO set. At moderate-to-high fee rates, outputs below the marginal cost of a Taproot input are effectively unspendable — yet they are counted as received funds and dragged into every aggregation/spend transaction, silently reducing the distributable balance. This mirrors the report exactly: the system "has" balance it cannot actually place, and the distribution path has no success/failure signal — `scan_transaction` happily reports the dust, and `SignableTransaction::new` happily includes it until fees consume it.

### Likelihood Explanation
Triggering requires only sending Bitcoin transactions to a known vault address — fully within the reach of an unprivileged party. The cost to the attacker is bounded (dust-valued outputs), while the cleanup cost falls on the multisig. No threshold collusion, malicious node, or leaked key is needed.

### Recommendation
In `scan_transaction`/`scan_block` (or at the `ReceivedOutput` consumption boundary), reject outputs whose `value` is below a spendability threshold derived from `DUST` and the marginal input fee at a configurable fee rate, and skip coinbase outputs (`block.txdata[0]`) or mark them immature until 100 confirmations. Alternatively, carry a spendability flag on `ReceivedOutput` so the scheduler can exclude dust rather than silently absorbing it into `input_sat`.

### Proof of Concept
1. Obtain the vault's P2TR `script_pubkey` from any prior vault transaction (public data).
2. Broadcast a transaction with an output paying, e.g., 500 sats to that script (below `DUST` = 546 and below the fee to spend it at typical fee rates).
3. Once confirmed, `Scanner::scan_block`/`scan_transaction` returns a `ReceivedOutput` for it (`mod.rs:205-211`), and the wallet reports it as balance.
4. The scheduler's aggregation includes it as an input; `SignableTransaction::new` adds ~58 vbytes of weight for a 500-sat contribution — net-negative at fee rates above ~8 sat/vbyte — and repeats for every dust output the attacker created. The vault burns funds on each spend, or hits `NotEnoughFunds` on tight payments, with no error path indicating the output was never usable.