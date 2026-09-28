### Title
Attacker-spoofed "branch" UTXO triggers execution of Serai's queued payment plans - (File: processor/src/multisigs/scheduler/utxo.rs)

### Summary
The vulnerability class of the source report (attacker-controlled content presented as trusted internal state — Omnibox spoofing) maps onto Serai's UTXO scheduler: outputs are classified purely by `script_pubkey`, and the internal `Branch`/`Change`/`Forwarded` addresses are publicly computable from the group key (`key + G*offset`, with offsets derived from the public `hash_to_F(KEY_DST, ...)` constants in `processor/src/networks/bitcoin.rs:308-346`). An unprivileged user can send funds to the branch address with an attacker-chosen amount, and `Scheduler::add_outputs` will treat that forged `OutputType::Branch` UTXO as a Serai-created branch output, popping and executing the queued `payments` bound to that amount.

### Finding Description
`Scanner::scan_transaction` matches outputs only on `script_pubkey` (`networks/bitcoin/src/wallet/mod.rs:199-214`), and `get_outputs` tags each match with a `kind` derived from the offset (`processor/src/networks/bitcoin.rs:686-700`). Because `BRANCH_OFFSET`/`CHANGE_OFFSET`/`FORWARD_OFFSET` are deterministic public values, anyone can compute `address_from_key(key + G*BRANCH_OFFSET)` and fund it. In `add_outputs`, a UTXO with `kind() == OutputType::Branch` whose `balance().amount.0` equals a key in `self.plans` immediately executes `self.execute(vec![utxo], payments, ...)` — a plan intended to consume an output Serai itself created via `created_output` (`processor/src/multisigs/scheduler/utxo.rs:266-296, 461-524`). There is no binding between a branch plan and the specific outpoint/TX that was supposed to fund it; the only key is the amount.

### Impact Explanation
The multisig signs a transaction consuming an attacker-supplied input to satisfy Serai's internally scheduled payments — a plan execution triggered by a spoofed "internal" output that was never created by Serai. Concrete consequences:

- The scheduler's branch/plan accounting is corrupted: the plan bound to `amount` is consumed by the attacker's UTXO, so when Serai's real branch output later confirms it no longer finds its plan and is absorbed as general liquidity (`self.utxos.push(utxo)`), breaking the intended 1:1 branch→plan invariant and prematurely releasing queued payments outside the intended ordering/fee-amortization path.
- An attacker can force execution of pending payment bundles at a time of their choosing (e.g., before confirmations/retirement logic expects), since triggering requires only an on-chain UTXO of a matching amount to a publicly known address.

The attacker must fund the spoof UTXO themselves, so direct theft is not achieved; the impact is unintended signature production and scheduler state corruption — a Medium-severity analog of "presented as something it is not."

### Likelihood Explanation
Reachable by any unprivileged party: computing the branch address needs only the group key and public constants, and funding it requires a normal Bitcoin transaction. The attacker needs to guess/observe an `amount` present in `plans` — branch amounts are sums of queued payments visible in Serai's own on-chain transactions, so matching is practical. The amount-collision requirement and the fact that the attacker funds the input keep this from being High.

### Recommendation
Bind branch plans to the exact outpoint Serai created: record the expected `OutPoint`/txid when `created_output` registers `plans`, and in `add_outputs` only execute a branch plan when the UTXO's outpoint matches the recorded one (or verify the UTXO descends from a Serai-signed transaction). Alternatively, authenticate internal outputs by requiring them to be produced by plans the scheduler itself emitted rather than trusting `script_pubkey` classification alone.

### Proof of Concept
1. Observe the multisig's group key `key` and compute `branch = address_from_key(key + G * Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"branch"))` — equivalent to `Bitcoin::branch_address(key)` (`processor/src/networks/bitcoin.rs:333-336, 671-674`).
2. Observe a Serai transaction paying the branch address with amount `A` (a plan being created), so `plans[A]` is populated after `created_output` runs.
3. Broadcast an attacker-funded transaction with an output `{script_pubkey: branch_script, value: A}`.
4. On the next `get_outputs`/`schedule`, `add_outputs` sees `utxo.kind() == OutputType::Branch` and `self.plans.contains_key(&A)`, pops the queued `payments`, and produces `Plan { inputs: [attacker_utxo], payments, ... }` — the multisig signs a spend of attacker funds executing Serai's queued payments, while Serai's legitimate branch UTXO is left plan-less and folded into `self.utxos`.