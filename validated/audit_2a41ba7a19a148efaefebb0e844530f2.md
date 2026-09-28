### Title
Scanner accepts zero-value/dust outputs which are then spent at a net loss, draining multisig funds via forced uneconomic input inclusion - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to "price returns 0 instead of reverting, letting `mint` proceed for free": `Scanner::scan_transaction` accepts any output paying to a registered script regardless of value — including zero-value and dust outputs — with no minimum-amount check. The resulting `ReceivedOutput`s are indistinguishable from economic ones and flow into `SignableTransaction::new`, which spends them as inputs. Each input costs ~57+ vbytes of fee at the `fee_per_vbyte` rate; any received output below that cost produces a transaction whose inputs destroy more value than they contribute, i.e., a "free" action (attacker pays dust) that forces a real cost on the threshold wallet.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `Scanner::scan_transaction` matches only on `output.script_pubkey` and unconditionally records the output's value:

- `scan_transaction` pushes a `ReceivedOutput` for every matching output with no lower bound on `output.value` (networks/bitcoin/src/wallet/mod.rs:199-214).
- `scan_block` applies this to every transaction in a block (mod.rs:221-227); the only documented caveat is coinbase maturity — value is never considered.
- `ReceivedOutput` stores `output.value` opaquely; `read`/`write` round-trip any TxOut, including `Amount::ZERO` (mod.rs:122-148).

On the spend side, `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-255) checks payment *outputs* against `DUST` (send.rs:165-169) but applies no equivalent minimum to *inputs*. Every input in `inputs` becomes a `TxIn` (send.rs:177-185), contributes its `input.output.value` to `input_sat` (send.rs:175), and adds a fixed ~57-vbyte weight to `needed_fee` via `calculate_weight_vbytes` (send.rs:62-127, comment at send.rs:619-620 of processor shows ~230 WU ≈ 57 vB per input). For an input of value `v` and fee rate `f`, inclusion is only profitable when `v > 57·f`; at typical minimum relay fee (1 sat/vB) any output below ~57 sats is a guaranteed loss, and at realistic fee rates the uneconomic threshold is far higher (hundreds–thousands of sats). Nothing prevents such an input from being signed and broadcast: `TransactionSignMachine::sign` produces valid sighashes for it (send.rs:373-397).

Like the original report's `getPrice` returning 0 rather than reverting, the scan path "returns" a spendable-looking output with value ≤ the cost of spending it rather than rejecting it, and the mint/spend path never checks the boundary condition.

### Impact Explanation
An unprivileged Bitcoin user can send arbitrarily small (dust, even near-zero) outputs to a Serai P2TR address. Each is reported by the scanner as received funds, and once scheduled into a `SignableTransaction`, the threshold group signs a transaction paying more in incremental fee than the input contributes — a direct, repeatable drain on multisig funds at negligible attacker cost. It also pads transactions toward `MAX_STANDARD_TX_WEIGHT`/`MAX_INPUTS`, degrading throughput of legitimate payments.

### Likelihood Explanation
The attack requires only sending a standard Bitcoin transaction to a known deposit address — fully reachable by any unprivileged party with public inputs (a Bitcoin transaction they send), matching the reachability model. Whether the processor-side scheduler aggregates these inputs automatically depends on code outside the strict scope, but within `networks/bitcoin/src` itself there is no defense: the scanner emits them and `SignableTransaction::new` will spend any `ReceivedOutput` it is given without a value floor.

### Recommendation
Enforce a minimum economically-spendable value when accepting outputs: either in `Scanner::scan_transaction`/`get_outputs` (reject or flag `output.value < MIN_INPUT_VALUE` where `MIN_INPUT_VALUE` covers worst-case input weight at a conservative fee rate, e.g. reusing the `DUST`/`10_000`-sat reasoning used for payments), or in `SignableTransaction::new` by erroring on inputs below the input-cost threshold. The check should mirror the existing `DustPayment` guard so value validation is symmetric between incoming and outgoing outputs.

### Proof of Concept
1. Obtain the multisig's external/forwarded address (`p2tr_script_buf(key)` / registered offset script).
2. Broadcast a Bitcoin transaction containing an output with `value = 1` sat (consensus-valid; relayable at ~330 sats for P2TR dust relay rules, or a miner can include any value) to that script.
3. `Scanner::scan_transaction` returns `ReceivedOutput { offset, output: TxOut { value: 1, .. }, .. }` — indistinguishable from a real deposit.
4. When scheduled, `SignableTransaction::new` adds it as an input, increasing `needed_fee` by `fee_per_vbyte · 57` while adding 1 sat to `input_sat`, decreasing the change output (or increasing the burned fee) by `57·fee_per_vbyte − 1` sats. The resulting transaction is valid, is signed by the FROST `TransactionMachine`, and broadcast — net loss to the multisig, repeated per dust output sent.

(Note: this is a Medium-severity analog — value acceptance without a lower bound causing forced uneconomic spends — rather than a direct "free mint"; I was unable to fully verify the processor scheduler's input-filtering behavior since it lies outside the in-scope crates.)