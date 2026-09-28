### Title
Attacker-inflated median fee rate causes the multisig to burn arbitrary amounts of its balance as transaction fees - (File: processor/src/networks/bitcoin.rs)

### Summary
The Frankencoin bug allowed a user-supplied price to unboundedly inflate a protocol-paid reward (`volume * CHALLENGER_REWARD`), draining reserves. The Serai analog lives in `Bitcoin::median_fee`: the fee rate applied to every Bitcoin multisig spend is derived from the per-transaction fee rates of *untrusted, attacker-includable transactions* in a single reference block. Just as the challenger-controlled `price` flowed unvalidated into the reward formula, the attacker-controlled fee rate of a block transaction flows unvalidated into `BSignableTransaction::new`'s `fee_per_vbyte`, which deterministically burns `fee_per_vbyte * vbytes` satoshis of multisig funds.

### Finding Description
`make_signable_transaction` calls `self.median_fee(&block_for_fee)` on whatever block is passed as `block_number`, then hands `fee.0` to `BSignableTransaction::new` as `fee_per_vbyte` (`processor/src/networks/bitcoin.rs:430-451`). `median_fee` collects `(in_value - out) / tx.vsize()` for every non-coinbase transaction in the block, sorts them, and picks `fees.get(fees.len() / 2)` (`processor/src/networks/bitcoin.rs:389-414`).

Two properties make this attacker-controlled:

1. The fee rate of each sampled transaction is set entirely by its sender. An unprivileged party that gets a transaction into the sampled block can attach a fee rate of any magnitude.
2. The "median" is not a robust median. `fees.get(fees.len() / 2)` on the sorted list selects the *upper* middle element for even-length lists (e.g. index 1 of 2 = the maximum) and the strict upper middle for odd lists. In a block with few transactions — e.g. coinbase + one honest tx + one attacker tx — a single attacker transaction with a huge feerate becomes (or exceeds) the selected element. An attacker can outright pick the returned fee by including one high-fee transaction in a low-traffic block, or a small number of them to dominate the median.

`SignableTransaction::new` then multiplies this rate by the transaction's vbytes (`needed_fee = fee_per_vbyte * vbytes`, `networks/bitcoin/src/wallet/send.rs:206`) and only requires `input_sat >= payment_sat + needed_fee` (line 215). There is no cap: any feerate the attacker injects that fits within the inputs' total value is accepted, subtracted from change/payments, and burned to the block's miner. The threshold multisig then FROST-signs this transaction — the fee is consensus-valid and irrevocable.

This mirrors the report's shape exactly: a value a party controls (position price / block tx feerate) is fed without bounds into a protocol payout formula (2% reward / `fee_per_vbyte * vbytes`), paid out of pooled funds, up to the entire balance held.

### Impact Explanation
All Bitcoin held by the multisig used as inputs to a `Plan` can be burned as fees, up to `sum(inputs) - payments - dust`, whenever the attacker can place a high-fee transaction into the block sampled by `make_signable_transaction`. If the attacker is (or bribes) a miner, the "reward" is directly collected — the same drain-reserves-for-profit outcome as H-06. Even without miner collusion it is a griefing drain of locked bridge funds. The converse direction also exists: an attacker dominating the median with near-zero-fee transactions drives `fee.0` below relay minimums, but `TooLowFee` panics (line 464-466), a liveness fault in every signer.

### Likelihood Explanation
Any Bitcoin user can send a transaction with an arbitrary feerate; doing so costs the fee itself only for the attacker's own output. Blocks routinely contain few transactions (especially in low-activity periods), and a one-transaction-plus-coinbase block makes `fees.len()/2 = 0` — wait, `fees` only collects `txdata[1..]`, so with a single attacker tx the median *is* the attacker's feerate outright. No validator status, collusion, or key material is needed; the attacker's only requirement is that their transaction lands in whichever block `block_number` resolves to, which the processor samples at Plan-execution time and cannot predict defensively.

### Recommendation
Bound `fee_per_vbyte` against a sanity ceiling derived from trusted data (e.g. `estimatesmartfee` from the node's mempool, or a hard protocol maximum sat/vbyte), and clamp or reject values above it before constructing `SignableTransaction`. Sample fee statistics over multiple blocks or use the node's mempool fee estimates rather than per-transaction feerates of a single block, and compute a true median (average of the two middle elements) at minimum.

### Proof of Concept
1. Observe/mine a Bitcoin block containing only the coinbase and the attacker's transaction `A`, where `A` pays a feerate of e.g. 1,000,000 sat/vbyte (attacker spends a small input back to themselves; on low-value scenarios the cost is bounded by `A`'s input size).
2. When the processor's `prepare_send`/`signable_transaction` calls `make_signable_transaction(block_number, ...)` where `get_block(block_number)` returns that block, `median_fee` computes `fees = [1_000_000]`, returns `Fee(1_000_000)`.
3. `BSignableTransaction::new(..., fee_per_vbyte = 1_000_000)` sets `needed_fee = 1_000_000 * vbytes` (`networks/bitcoin/src/wallet/send.rs:206`). For a ~200 vbyte transaction this burns 200,000,000 sat (~2 BTC) — bounded only by `input_sat` (line 215), i.e. the multisig's own UTXOs.
4. The FROST multisig signs and broadcasts the transaction; the fee is paid to the miner (potentially the attacker). Funds are irrecoverable — analogous to the report's `zchf.totalSupply` inflation paid to the challenger.