### Title
Unbounded Fee Oracle Lets External Block Producers Force Serai To Burn Vault Funds As Fees - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::median_fee` derives the fee rate used to construct `SignableTransaction`s from the median fee rate of a **single** Bitcoin block, with no upper bound. `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` then applies `fee_per_vbyte * vbytes` directly as the fee, sending everything else to the change output. There is no sanity cap on `fee_per_vbyte`. An external block producer (or natural congestion spike) that fills a block with high-fee transactions raises the median arbitrarily, causing Serai to sign a transaction paying up to `sum(inputs) - sum(payments)` in fees — the same "no slippage bound → loss of user funds" class as the Uniswap V3 `amountOutMinimum = 0` finding.

### Finding Description
`make_signable_transaction` fetches the fee from exactly one block (`get_block(block_number)` then `median_fee`), and `median_fee` averages nothing, clamps nothing, and returns `fees[len/2]` of all non-coinbase transactions in that block (`processor/src/networks/bitcoin.rs:388-415`, `429-431`). The only bounds applied are a `.max(1)` floor and the dust/minimum-relay checks inside `SignableTransaction::new` — the effective "slippage cap" on the fee is entirely absent (`networks/bitcoin/src/wallet/send.rs:206-234`).

```rust
// processor/src/networks/bitcoin.rs
let fee = fees.get(fees.len() / 2).copied().unwrap_or(0);
Ok(Fee(fee.max(1)))                    // unbounded above
```

```rust
// networks/bitcoin/src/wallet/send.rs
let mut needed_fee = fee_per_vbyte * vbytes;
...
if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
  if value >= DUST {
    tx_outs.push(TxOut { value: ..., script_pubkey: change });
```

The signed transaction commits to this fee via `Prevouts::All` + `TapSighashType::Default` (`send.rs:373-389`), so once planned, the threshold signs and publishes a transaction whose actual fee is `sum(prevouts) - sum(outputs)` (`send.rs:138-141`). A miner can mine a block stuffed with self-funded transactions paying, e.g., 10,000 sat/vB (the fees return to the miner, so cost is only block space/opportunity), and Serai's next `signable_transaction` call adopts that rate, paying ~`inputs - payments` to whichever miner confirms it — a permanent loss of vault BTC mirrored exactly by the report's "all reward tokens lost to unbounded adverse price" impact.

### Impact Explanation
Loss of protocol funds proportional to input size. For plans with change, `needed_fee` absorbs up to `sum(inputs) - sum(payments) - DUST`; for plans without change, the entire leftover is fee already (`send.rs:145-147`), so an inflated rate just determines how much of that leftover is "justified" — either way the vault's BTC is burned to miners. Unlike the dust-drop edge (bounded to 545 sats), this path is bounded only by the plan's input value.

### Likelihood Explanation
Requires either (a) a block producer actively stuffing one block with high-fee transactions — cheap for the miner since fees are self-paid — or (b) transient fee spikes, which produce the same overpayment without any attacker (the bug still bites, paying far above market on a later, cheaper block). Because the oracle samples a single block with no averaging, cap, or floor beyond `max(1)`, no persistence of manipulation is needed. Medium likelihood, high magnitude.

### Recommendation
Cap `fee_per_vbyte` at a protocol-defined maximum (e.g., a multiple of a long-run median or an absolute sat/vB ceiling) and/or average the median over several recent blocks. Reject or defer plan execution when the sampled fee exceeds the bound, rather than signing the overpaying transaction. This is the Serai analog of enforcing `amountOutMinimum`/deadline on the swap.

### Proof of Concept
1. Attacker mines a Bitcoin block where >50% of non-coinbase transactions are their own, each paying fee rate R ≫ normal (fees recycle to the attacker-miner).
2. Serai's next `prepare_send`/`signable_transaction` calls `get_block(block_number)` on that block; `median_fee` returns ~R (`bitcoin.rs:388-415`).
3. `SignableTransaction::new` computes `needed_fee = R * vbytes`; the change output receives `input_sat - payment_sat - needed_fee` (`send.rs:224-234`).
4. The FROST threshold signs via `Prevouts::All` (`send.rs:375`), publishing a TX paying ~R sat/vB. Any confirming miner collects `sum(inputs) - sum(outputs)` — vault funds permanently lost, with no bound enforced anywhere in the pipeline.