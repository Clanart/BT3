### Title
`Scanner::scan_block` reports outputs created and spent within the same block as received, crediting funds that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` matches outputs solely by `script_pubkey`, and `Scanner::scan_block` accumulates every matching output across all transactions in a block without ever removing outputs that are spent by later transactions in the same block. An unprivileged external party can send a Bitcoin transaction paying a registered Serai script (external/branch/change/forward address) and spend that output with a second, chained transaction mined in the same block (Bitcoin permits intra-block spend chains). The scanner still emits a `ReceivedOutput` claiming ownership of a UTXO that no longer exists — analogous to `cancelAuction` deleting the auction without refunding `currentBid`: value moves through the system, and the code records the incoming leg while ignoring the outgoing leg.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `scan_transaction` iterates `tx.output` and pushes a `ReceivedOutput` for any `output.script_pubkey` present in `self.scripts` (lines 199–214). It never inspects `tx.input`. `scan_block` (lines 221–227) then extends the result over every transaction in `block.txdata`, so an output created by `txdata[i]` and consumed by `txdata[j]` (j > i) is still returned as a live `ReceivedOutput` with a valid `outpoint`, `output`, and `offset`.

The result type, `ReceivedOutput`, is exactly what `SignableTransaction::new` consumes as `inputs` (send.rs lines 150–156): it copies `input.outpoint` into `TxIn`s, sums `input.output.value` as available funds, and later `multisig()`/`sign()` produce FROST-signed witnesses spending those outpoints (send.rs lines 273–285, 373–397). Nothing in the pipeline re-validates that the outpoint is still unspent.

The doc comment on `scan_block` (lines 216–220) documents only the coinbase-maturity caveat; the intra-block spend case is undocumented and unchecked.

### Impact Explanation
Downstream consumers treat each `ReceivedOutput` as spendable balance: `SignableTransaction::new` builds inputs from `outpoint`s and credits `output.value` toward payments (send.rs lines 175–185). For an output already spent in the same block, the node reports "funds received" that do not exist on-chain as a UTXO. Concretely: (1) balance/deposit accounting credits value that was immediately moved away by the sender, enabling a double-credit primitive against any integrator that trusts scanner output; (2) the threshold network is driven through a full FROST signing ceremony producing a transaction the Bitcoin network rejects (spending a nonexistent UTXO), burning preprocesses/signing rounds on a permanently invalid spend. This matches the accepted impact class "funds reported received that are not spendable."

### Likelihood Explanation
Fully reachable by an unprivileged party: the attacker only needs to broadcast two ordinary Bitcoin transactions — one paying a known Serai address (external/branch/change/forward scripts are derivable from the public group key via `p2tr_script_buf` and the public hash-to-F offsets), and a second spending it — that get mined in the same block. Intra-block transaction chaining is standard Bitcoin behavior and requires no miner cooperation beyond inclusion ordering (parent before child). The triggering condition (payment + spend in one block) is a single-block race the attacker fully controls.

### Recommendation
In `scan_block` (and any batched scan), track outpoints consumed by `tx.input` earlier in the same block and filter emitted `ReceivedOutput`s whose `outpoint` was spent within the scanned block — i.e., first collect all spent `OutPoint`s from every input in `block.txdata`, then drop matching received outputs. This mirrors the original report's fix: account for the outgoing leg (like returning `currentBid`) rather than only recording the incoming one.

### Proof of Concept
1. External party obtains a registered Serai script `S` (e.g., the external address `p2tr_script_buf(group_key)`, or branch/change/forward via the public `hash_to_F(KEY_DST, ...)` offsets).
2. Broadcast `txA` with `txA.output[0] = TxOut { script_pubkey: S, value: V }`, and `txB` whose `txB.input[0].previous_output = OutPoint(txid(txA), 0)` spending `V` to an attacker address; ensure both mine in block `B` with `txA` before `txB`.
3. `Scanner::scan_block(B)` iterates `txdata`, `scan_transaction(txA)` pushes `ReceivedOutput { offset, output: TxOut{S, V}, outpoint: (txidA, 0) }`; `scan_transaction(txB)` finds no matching outputs and nothing removes the earlier entry. Result: `V` reported received.
4. Feeding this `ReceivedOutput` into `SignableTransaction::new(vec![output], payments, ...)` succeeds (it passes `NotEnoughFunds`), and `multisig()`/`sign()`/`complete()` produce a fully signed transaction spending a nonexistent UTXO — rejected by Bitcoin, while the scanner already credited `V`.