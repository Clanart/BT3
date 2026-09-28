### Title
Hard-coded dust threshold causes unspendable outputs / silently burned change - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` applies a single hard-coded `DUST` constant of 546 sats to every payment output and to the change check, regardless of the actual serialized size of each output's `script_pubkey`. Bitcoin Core's `GetDustThreshold` computes the dust limit as `3 * minRelayFee * (serialized_output_size + input_size)`, which scales with the script size and differs per output type (P2TR's dust is 330 sats; larger scripts have proportionally higher thresholds). This is the same bug class as the Cooler report's hard-coded `1e18` decimal factor: a fixed precision/size constant substituted for a value that actually varies with its inputs, producing wrong value calculations that lose user funds.

### Finding Description
At `networks/bitcoin/src/wallet/send.rs:32`, `pub const DUST: u64 = 546` is defined and the comment acknowledges it ignores the SegWit vs. non-SegWit distinction "due to how marginal these values are". The constant is then used in two places inside `SignableTransaction::new`:

- `networks/bitcoin/src/wallet/send.rs:165-169` — every payment amount is compared against `DUST`. A payment of ≥546 sats to an output whose real dust threshold is higher (e.g., a large/arbitrary `ScriptBuf`, since `payments` is caller-supplied `(ScriptBuf, u64)` pairs) passes this check but is dust under relay policy, making the whole transaction non-standard.
- `networks/bitcoin/src/wallet/send.rs:224-234` — when a change address is provided, the leftover `input_sat - payment_sat - fee_with_change` is only kept as a change output if `value >= DUST`; otherwise it is silently absorbed into the fee. Additionally, because the payment dust check uses the constant rather than a per-output threshold, a change output to a large script of, e.g., 546 sats can also be dust, again producing an unrelayable transaction that consumes the inputs' signatures.

The unprivileged caller controls the `payments` scripts and amounts, so they can construct transactions that pass `SignableTransaction::new`'s validation yet are non-standard and will never propagate/confirm.

### Impact Explanation
Two fund-affecting outcomes:

1. **Unspendable/never-confirming transactions**: A payment output that satisfies `>= 546` sats but is below its script-dependent dust threshold makes the entire signed transaction non-standard under `CTxMemPool` policy. The threshold signers produce a valid signed transaction (`complete` at line 413 returns it), yet the funds are not moved; the inputs remain locked in the wallet's accounting until the attempt is abandoned.
2. **Silent fee burn**: With a change address set, any leftover `value < DUST` (up to 545 sats per transaction) is added to the miner fee rather than returned, even though for SegWit/Taproot outputs the true dust floor is ~330/294 sats — leftover amounts in the 330–545 range that are actually valid change outputs are burned to miners instead.

This matches the accepted impact "funds reported received that are not spendable" / incorrect calculation causing loss of funds.

### Likelihood Explanation
The path is fully reachable by the caller of `SignableTransaction::new`: `payments` is an arbitrary `&[(ScriptBuf, u64)]` slice with no restriction to standard templates, so any output script larger than a P2PKH-P2SH baseline raises its dust threshold above 546, and any sub-546 leftover with a change address hits the fee-burn branch. Both require only ordinary public inputs (payment script and amounts), no validator collusion.

### Recommendation
Replace the global `DUST` constant with a per-output dust calculation mirroring `bitcoin::policy`/`GetDustThreshold`: compute `serialized_size(output)` for each `TxOut` (8-byte value + compact-size-prefixed `script_pubkey` + the spending-input witness/base cost per BIP-341) and compare each payment and the change output against its own threshold. For change, only drop the output when it is below *its* computed dust, not the fixed 546.

### Proof of Concept
1. Build `SignableTransaction::new` with an input worth e.g. 100_000 sats and a payment `(script, 546)` where `script` is a large `ScriptBuf` (e.g., a bare multisig or wrapped script with serialized output size > ~107 bytes such that `3 * fee_rate * (size + 148) > 546`).
2. The check at line 166 (`*amount < DUST`) passes since 546 is not `< 546`.
3. `fee()`/`needed_fee` pass, the transaction is signed via `TransactionSignMachine::sign`, and `complete` returns a valid `Transaction` — which every relay policy node rejects as dust, so the "payment" never confirms despite the library reporting a valid, signed spend.
4. For the change path: inputs = `payment_sat + fee + 400` with a P2TR change script (real dust ~330). Line 229's `value >= DUST` (546) fails, so the 400 sat leftover — a perfectly valid, spendable output — is silently converted into extra fee paid to miners.