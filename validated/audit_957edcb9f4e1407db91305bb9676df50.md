### Title
Flat `DUST` threshold allows dust outputs to large scripts, producing transactions that fail relay - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
The external report's bug class is a **hardcoded resource limit applied uniformly to all recipients** (`transfer()`'s fixed 2300 gas) which causes sends to certain recipients to always fail. The analog in Serai's in-scope code is the flat `DUST = 546` check in `SignableTransaction::new`: Bitcoin's actual dust threshold scales with the serialized size of the output's `script_pubkey`, so payments to recipient scripts larger than the standard ~34-byte Taproot script can pass Serai's dust check while still being dust under network policy. Any `TxOut` produced this way makes the *entire signed transaction* non-standard and unrelayable — the transfer fails exactly as in the original report.

### Finding Description
`SignableTransaction::new` validates each payment only against a constant dust floor:

- `networks/bitcoin/src/wallet/send.rs:32` — `pub const DUST: u64 = 546;`
- `networks/bitcoin/src/wallet/send.rs:165-169` — `for (_, amount) in payments { if *amount < DUST { Err(TransactionError::DustPayment)?; } }`
- `networks/bitcoin/src/wallet/send.rs:188-191` — payment `(ScriptBuf, u64)` pairs are turned into `TxOut`s verbatim, with no per-output dust evaluation.

The comment at `send.rs:29-31` acknowledges the simplification ("doesn't bother with delineation") but only considers the SegWit-vs-legacy distinction (~546 vs ~540). Bitcoin Core's real dust test is `dustThreshold = 3 * dustRelayFee/1000 * (GetSerializeSize(txout) + spend_size)`: for a key-path-spendable or even unspendable output whose `script_pubkey` is, say, 100–400 bytes (nonstandard bare scripts, large push-only scripts, `OP_RETURN`-adjacent constructions produced via `Address::new(script_pubkey)` in `processor/src/networks/bitcoin.rs:728`), the threshold is several times 546 sats. A payment of, e.g., 546–900 sats to such a script passes `SignableTransaction::new`, gets included in `tx_outs` (`send.rs:188-191`), the multisig signs it via `TransactionSignMachine::sign` (`send.rs:355-398`), and the resulting transaction is rejected by every default-policy node as dust — the transfer can never confirm.

### Impact Explanation
An unprivileged user controls payment destinations through burn/`OutInstruction` flows: the payment `ScriptBuf` is attacker-chosen data. Because payments are batched into shared transactions (the UTXO scheduler packs multiple `Payment`s into one `Plan`, `processor/src/multisigs/scheduler/utxo.rs:198-264`), a single sub-threshold output to an oversized script poisons the whole transaction: every other payment in the plan and the change output fail to broadcast, while Serai has already committed to and signed the plan. This is the direct analog of "transfer will fail if the recipient uses more than 2300 gas": a fixed, recipient-independent limit (`DUST`) is applied where the real limit is recipient-dependent (`script_pubkey` size), causing transfers to a reachable class of recipients to permanently fail and freezing co-batched funds.

### Likelihood Explanation
Reaching it requires only crafting a burn/withdrawal to a Bitcoin `Address` whose underlying `script_pubkey` is large enough that its dust threshold exceeds the chosen payment amount in sats — no validator collusion, no privileged access, no malformed cryptographic inputs. `Address::new` accepts arbitrary script buffers, and `SignableTransaction::new` performs no script-size-aware check, so the condition is deterministic once such a payment is scheduled.

### Recommendation
Replace the flat `DUST` constant with a per-output dust calculation mirroring Bitcoin Core's policy: compute `GetSerializeSize(txout) + input spend size` for each payment's actual `script_pubkey` and compare `amount` against `3 * dustRelayFee * size / 1000`. Reject (or refuse to schedule) any payment that would be dust under its own script's threshold, so a bad recipient script fails at plan-creation time instead of invalidating a fully signed batch transaction.

### Proof of Concept
1. Construct a payment `payments = [(script, 600)]` where `script` is a valid `ScriptBuf` of ~200 bytes (e.g., a bare script with large pushes, reachable via `Address::new(script)` on the processor's burn path).
2. Call `SignableTransaction::new(inputs, &payments, change, None, fee_rate)` with sufficient input value.
3. The check at `send.rs:165-169` passes since `600 >= DUST (546)`, and the output is emitted into `tx.output` at `send.rs:188-191`.
4. The multisig completes signing (`TransactionSignatureMachine::complete`, `send.rs:413-428`), producing a transaction whose ~600-sat output is below the ~890+-sat dust threshold for a 200-byte script. Broadcasting via `send_raw_transaction` fails with `dust` / `non-standard`, so the signed plan — including all other payments batched with it — can never confirm.