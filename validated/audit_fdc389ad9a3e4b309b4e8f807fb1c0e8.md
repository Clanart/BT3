### Title
Immature coinbase donations are reported as spendable wallet outputs - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`Scanner::scan_block` scans every transaction in a Bitcoin block, including the coinbase transaction, and returns matching outputs as ordinary `ReceivedOutput` values. `ReceivedOutput` retains only the spending offset, `TxOut`, and `OutPoint`; it does not record that the output came from a coinbase or require the 100-block coinbase maturity period. A downstream caller can pass such an output to `SignableTransaction::new`, which accepts and signs it without checking maturity, causing wallet accounting to treat unavailable block subsidy as spendable funds.

### Finding Description
`Scanner::scan_transaction` matches any transaction output whose `script_pubkey` is present in the scanner’s script map and constructs a `ReceivedOutput` containing the output’s offset, value, and outpoint. `Scanner::scan_block` invokes this logic for every transaction in `block.txdata`, including `block.txdata[0]`, the coinbase transaction.

The resulting `ReceivedOutput` has no coinbase/maturity marker. `SignableTransaction::new` later converts each supplied `ReceivedOutput` directly into a `TxIn` and stores its `TxOut` in `prevouts`, without checking whether the referenced outpoint is a mature coinbase output. Consequently, a miner can donate an output to a scanned wallet in a coinbase transaction, and the wallet API reports it as an ordinary spendable output before it can legally be spent.

This is analogous to the donation/accounting issue: externally injected value enters the wallet’s local accounting through a path that does not preserve a crucial property of the funds. Here, the missing property is spendability due to coinbase maturity.

### Impact Explanation
A wallet or accounting layer using `scan_block` can report funds as received and available even though Bitcoin consensus prevents spending them for 100 blocks. If the output is selected by `SignableTransaction::new`, the threshold signing flow can produce a transaction spending an immature coinbase outpoint, which peers and miners will reject.

The donated amount is therefore included in locally spendable inventory while being temporarily unusable. Depending on the caller, this can cause failed withdrawals, stuck transaction plans, or incorrect available-balance reporting.

### Likelihood Explanation
The trigger requires an attacker or miner to place an output to a monitored script in a coinbase transaction and have that block accepted by the wallet’s followed chain. This is not available to every Bitcoin user at arbitrary times, but it is a public on-chain input path and does not require validator compromise or access to Serai secrets.

The issue also requires the consuming code to call `scan_block` directly rather than manually excluding `block.txdata[0]` or separately filtering immature coinbase outputs. The API documentation notes the maturity burden, but the returned type does not encode or enforce the distinction, making incorrect consumption possible.

### Recommendation
Do not return coinbase outputs from `Scanner::scan_block` as ordinary `ReceivedOutput`s, or extend `ReceivedOutput` with maturity metadata that `SignableTransaction::new` validates. Prefer skipping `block.txdata[0]` internally and providing a separately named API for scanning immature coinbase outputs. Any output derived from a coinbase transaction should remain ineligible for transaction construction until the required maturity depth is reached.

### Proof of Concept
1. Instantiate `Scanner::new` for a spendable even Taproot key.
2. Miner-controlled block `B` has `B.txdata[0]` pay to the scanner’s P2TR script.
3. Call `Scanner::scan_block(B)`. The implementation iterates over every transaction and returns the coinbase output as a `ReceivedOutput`.
4. Pass that `ReceivedOutput` to `SignableTransaction::new`. The function uses its `outpoint` and `TxOut` as a normal input without checking that the output is an immature coinbase subsidy.
5. The resulting transaction is constructed and can be signed, but Bitcoin nodes reject it until the coinbase reaches maturity.