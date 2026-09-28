### Title
`Scanner::scan_block` credits immature coinbase outputs as spendable received funds — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The source report's bug class is "protocol credits the expected/declared amount rather than verifying the actual, usable balance received". In Serai's Bitcoin wallet, `Scanner::scan_block` scans every transaction in a block — including `block.txdata[0]`, the coinbase — and returns any matching outputs as `ReceivedOutput`s indistinguishable from ordinary, immediately spendable payments. A coinbase output is consensus-immature for 100 blocks, so funds are reported received that are not spendable.

### Finding Description
`Scanner::scan_transaction` iterates over `tx.output` and pushes a `ReceivedOutput` for every output whose `script_pubkey` matches a registered script (the base key or a registered offset), carrying the nominal `value` and `offset`:

- `networks/bitcoin/src/wallet/mod.rs:199-214` — `scan_transaction` builds `ReceivedOutput { offset, output, outpoint }` purely from a script match.
- `networks/bitcoin/src/wallet/mod.rs:221-227` — `scan_block` extends this over `block.txdata` starting at index 0, i.e. the coinbase transaction. The doc comment acknowledges this: "This will also scan the coinbase transaction which is bound by maturity."

The `ReceivedOutput` produced is structurally identical to one from a normal payment: `offset()`, `value()`, `outpoint()`, and `output` all look valid, and it round-trips through `ReceivedOutput::read`/`serialize` (as exercised in `networks/bitcoin/tests/wallet.rs:65-77`, which literally asserts `outputs == scanner.scan_transaction(&block.txdata[0])` on a coinbase). Downstream, `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) consumes `ReceivedOutput`s, sums `input.output.value` into `input_sat` (line 175), and the multisig would sign a spend of an outpoint that Bitcoin consensus rejects until maturity — the analog of the DEX "receiving less than it accounts for": the scanner's accounting (nominal satoshis received) exceeds the actual spendable balance.

The processor-side `get_outputs` (`processor/src/networks/bitcoin.rs:691`) deliberately skips `block.txdata[1 ..]`'s zeroth element for exactly this reason, proving the maturity hazard is real and that `scan_block` leaking coinbase outputs to any other caller is an inconsistent, unsafe default.

### Impact Explanation
Any caller of `Scanner::scan_block` (the library's documented block-scanning API) receives coinbase `ReceivedOutput`s with no marker distinguishing them from spendable ones. Feeding such an output into `SignableTransaction::new` produces a transaction the threshold group will validly sign (the key/offset math is correct), but which every Bitcoin node rejects as a premature coinbase spend — and, more importantly, the wallet's internal balance accounting over-credits `input_sat` by the coinbase value. If change computation relies on that inflated `input_sat`, the constructed transaction misallocates value (the "balance difference" never materializes on-chain). Impact is bounded to premature-spend rejection / mis-accounting rather than silent theft, keeping it Medium at most.

### Likelihood Explanation
Reachable by an unprivileged party: mining is permissionless, and any miner can direct a coinbase (or any nonstandard coinbase-like crediting tx at index 0) to a Serai multisig's P2TR script or a registered offset script. No collusion, key material, or RPC compromise is required — just a Bitcoin transaction the attacker causes to exist. Exploitation additionally requires the consuming code to use `scan_block` results without the documented post-processing maturity filter, which the API itself does not enforce.

### Recommendation
Have `Scanner::scan_block` skip `block.txdata[0]` (mirroring `processor/src/networks/bitcoin.rs:691`), or tag `ReceivedOutput`s mined in the coinbase so callers can filter by maturity rather than relying on an out-of-band "post-processing pass" the type does not encode. Alternatively, record block height/coinbase-ness in `ReceivedOutput` so `SignableTransaction::new` can reject immature inputs.

### Proof of Concept
`networks/bitcoin/tests/wallet.rs:40-77` already demonstrates the behavior: `send_and_get_output` mines a block paying the scanner's key via `generatetoaddress` (a coinbase output), then `scanner.scan_block(&block)` returns it and the test asserts `outputs == scanner.scan_transaction(&block.txdata[0])` — i.e. the coinbase output is indistinguishably reported as a received, spendable-looking `ReceivedOutput` with `offset() == Scalar::ZERO` and the full coinbase `value()`. Only the subsequent 100-block `generatetoaddress` mining in the test makes it actually spendable; `scan_block` itself provides no such guard.