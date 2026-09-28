### Title
Bitcoin withdrawal payouts are reduced/dropped by on-chain fee conditions at signing time with no minimum-output bound specified by the payer - (File: processor/src/networks/mod.rs)

### Summary
When Serai pays out a Bitcoin withdrawal (a `Payment` derived from a user's burn), the actual output amount the user receives is computed at transaction-construction time by amortizing the estimated network fee across the plan's payments in `Network::prepare_send` (processor/src/networks/mod.rs). The fee is derived from `Bitcoin::median_fee` of a recent block (processor/src/networks/bitcoin.rs:388-414) — i.e., from attacker-influenceable on-chain data. If the fee pushes a payment below `Self::DUST`, it is set to 0 and silently dropped ("dropping dust payment"), leaving the user with no Bitcoin payout even though the burn already consumed their funds. There is no `min_amount_out`-style parameter bound to the payment: the sender has no control over the minimum output the transaction must produce for them. This is the direct analog of the reported `DInterest.deposit` missing-`minInterestAmount` bug.

### Finding Description
The flow for a user-requested payout is:

1. The user burns on Serai, which produces a `Payment { address, balance }` scheduled via `Scheduler::schedule` (processor/src/multisigs/scheduler/utxo.rs:303-348).
2. `Network::prepare_send` (processor/src/networks/mod.rs:424-595) calls `needed_fee`, which for Bitcoin calls `make_signable_transaction` → `median_fee(&block_for_fee)` at the block number the processor happens to use (processor/src/networks/bitcoin.rs:429-431).
3. `median_fee` computes the median sat/vbyte fee over all transactions in that block — data any unprivileged party can influence simply by broadcasting high-fee Bitcoin transactions (processor/src/networks/bitcoin.rs:388-410).
4. `prepare_send` then subtracts this fee (plus accumulated operating costs, when a change output exists) from the user's payment amount (processor/src/networks/mod.rs:480-495), zeroes it if it falls below `Self::DUST` (mod.rs:497-502), and drops it entirely (mod.rs:519-530). The same reduction occurs for branch outputs in `Scheduler::created_output` (processor/src/multisigs/scheduler/utxo.rs:483-511), where a payment set can be dropped wholesale if the fee exceeds their sum.

At no point is there a bound on how much the output may be reduced. The signed transaction (and its `Eventuality`, which binds only the txid) commits to whatever amount the fee environment produces at that moment. State changes outside the payer's control — namely the composition and fees of the block sampled for `median_fee` — determine the user's output, exactly the front-running/output-shortfall class of the original report.

### Impact Explanation
An unprivileged party can inflate the sampled `median_fee` by sending high-fee Bitcoin transactions (a permitted public input per the scope rules). A victim's withdrawal can then be reduced to below dust and dropped, meaning the user receives nothing on Bitcoin despite having irrevocably burned their coins on Serai — a direct loss of funds with no minimum-received guarantee. Even without a targeted attacker, organic fee spikes produce the same outcome, and a dropped payment is not retried or refunded in this code path. This is a Medium-severity loss-of-funds issue contingent on fee-market manipulation.

### Likelihood Explanation
The attacker only needs to submit ordinary Bitcoin transactions with above-median fees so that the sampled block's median crosses the threshold that reduces a specific victim payment to dust. This is costly but bounded (the payment only needs to fall below `DUST = 10_000` sats), repeatable against any queued withdrawal, and requires no privileged position — no validator collusion, no leaked keys, only public mempool transactions.

### Recommendation
Bind a minimum acceptable output to each payment. Options:

- Include the expected post-fee amount (or an explicit `min_amount`) in the `Payment`/`Plan` committed at schedule time on Serai, and have `prepare_send`/`make_signable_transaction` refuse to produce a transaction whose output for that payment is below the bound — converting the plan into a retryable failure rather than a signed, under-funded payout.
- Cap the effective `fee_per_vbyte` used for amortization (e.g., clamp `median_fee` to a sane maximum) so an attacker cannot arbitrarily inflate the deducted fee.
- If a payment would fall below dust, keep it pending in the scheduler (`self.payments`) for a later, cheaper execution instead of zeroing and dropping it (mod.rs:497-530).

### Proof of Concept
1. Victim burns sriBTC for a Bitcoin withdrawal worth, e.g., `DUST + 5_000` sats; a `Payment` is scheduled (processor/src/multisigs/scheduler/utxo.rs:340).
2. Attacker observes the pending payout timing and floods the next Bitcoin block with high-fee self-transactions, raising `median_fee` (processor/src/networks/bitcoin.rs:388-410).
3. `prepare_send` amortizes the inflated fee across the victim's payment (processor/src/networks/mod.rs:482-495), pushing it under `Self::DUST`, so it is set to 0 and dropped (mod.rs:497-530); the transaction that gets threshold-signed contains no output for the victim.
4. The victim's burn is final on Serai, but no Bitcoin is received, and the dropped payment is not re-queued — permanent loss of funds caused entirely by state outside the sender's control.

Uncertain but assumed per scope: that `Payment`s originate from user burns/InInstructions as public inputs, and that dropped payments receive no later refund path — the scheduler code shows them simply discarded from the plan.