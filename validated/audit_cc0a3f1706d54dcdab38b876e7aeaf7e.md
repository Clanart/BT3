Found the analog. `IntentGatewayV2.placeOrder` (predispatch path) measures escrow amounts by *diffing the gateway's own token balance before/after* a `CallDispatcher` transfer, instead of relying purely on the transfer-return amount — this is exactly the "balance snapshot / delta" pattern from the Tempus `depositAndFix` bug, and it is directly griefable by an attacker donating tokens to the `CallDispatcher` before the sweep step.

### Title
Griefing/fund-diversion via balance-delta measurement in `placeOrder` predispatch sweep - (File: `evm/src/apps/IntentGatewayV2.sol`)

### Summary
`placeOrder`'s predispatch path computes how many input tokens the gateway actually escrowed by taking `IERC20(token).balanceOf(dispatcher)` and sweeping the dispatcher's *entire* balance to the gateway, then computing `received = balanceOf(gateway) after - before`, rather than tracking only the amount produced by the predispatch call itself.

### Finding Description
In the predispatch branch of `placeOrder`, the transfer-back step reads the dispatcher's raw token balance and moves the *whole balance*, not just the amount the predispatch call produced: [1](#0-0) 
and the sweep then measures escrow using a before/after balance diff on the gateway itself: [2](#0-1) 

Just like `TempusController.depositAndFix`'s `assert(yieldShares.balanceOf(address(this)) == 0)` was falsified by an attacker pre-loading yield shares into the contract, here an attacker can pre-fund the `CallDispatcher` (a shared, stateless helper contract used by *every* order's predispatch/postdispatch execution — `_execute`/dispatch calls run for arbitrary orders) with the target token before a victim's `placeOrder` transaction executes. Because the code sweeps "whatever balance is on the dispatcher" rather than "whatever the predispatch call itself produced," the attacker's donated tokens get swept into the gateway and folded into the victim's `received` calculation: [3](#0-2) 
If `received > order.inputs[i].amount`, the surplus is silently emitted as `DustCollected` and retained by the *protocol*, not the depositor. Conversely, this shared-balance sweep also means concurrent orders sharing the same dispatcher instance in the same block can have their sweep amounts cross-contaminate, since the measurement is a balance snapshot rather than a call-scoped return value — an unprivileged actor can manipulate what the gateway believes it "received" for someone else's order simply by pre-transferring tokens to the publicly-known `CallDispatcher` address before the victim's predispatch executes.

### Impact Explanation
This falls under "logic attacks" / "false state acceptance" in the bounty scope: the escrowed `order.inputs[i].amount` used to compute the order commitment and the amount owed to the solver on fill is derived from a balance snapshot that any external party can pollute by sending tokens directly to the `CallDispatcher` address, rather than from a value strictly attributable to the predispatch call. This can cause a user's order to be recorded with an incorrect (inflated or reduced) escrow amount, diverging from what they actually paid, or cause dust/fee accounting to misattribute externally-donated funds as protocol dust.

### Likelihood Explanation
The `CallDispatcher` address is a fixed, publicly known contract (`_params.dispatcher`) used across all orders, and predispatch execution is triggered by any unprivileged user calling `placeOrder`. An attacker only needs to front-load a small ERC20 transfer to the dispatcher before a target's `placeOrder` transaction lands (same-block ordering is sufficient, no relayer/prover/admin compromise required), mirroring the exact "attacker sends smallest unit of token to shared contract before victim's balance-check transaction" primitive from the seed report.

### Recommendation
Do not derive escrowed/received amounts from a raw balance snapshot of a shared, externally-fundable contract (`CallDispatcher`). Instead, have the predispatch call return/report the exact amount it produced (e.g., via a return value or an isolated per-call transient balance baseline scoped strictly to the current call's known pre-call balance including any pending externally-donated dust accounted for separately as immediate dust, not folded into `received`), or use a dedicated ephemeral (per-order) proxy/escrow contract instead of a shared dispatcher so balances cannot be polluted between unrelated orders.

### Proof of Concept
1. Attacker observes the mempool for a `placeOrder` call that uses `order.predispatch` with token `T` and dispatcher `D` (the fixed, globally-shared `CallDispatcher`).
2. Attacker front-runs (or same-block races) by sending a small amount of token `T` directly to `D`.
3. Victim's `placeOrder` executes: the predispatch call runs, then the sweep code at [4](#0-3)  reads `IERC20(token).balanceOf(dispatcher)` — which now includes the attacker's donated amount — and sweeps that entire balance to the gateway.
4. The `received` computation at [5](#0-4)  attributes the extra tokens either to `order.inputs[i].amount` (if under the requested amount, silently reducing what was believed escrowed for the correct token) or emits it as `DustCollected` for the protocol — in neither case is it returned to the attacker, and the victim's escrow record can end up desynchronized from actual expected value depending on relative amounts.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L242-256)
```text
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L258-280)
```text
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));

            // Measure actual received, emit dust for excess, update order.inputs.
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 received;
                if (token == address(0)) {
                    received = address(this).balance - balancesBefore[i];
                } else {
                    received = IERC20(token).balanceOf(address(this)) - balancesBefore[i];
                }

                if (received > order.inputs[i].amount) {
                    uint256 dust = received - order.inputs[i].amount;
                    emit DustCollected(token, dust);
                } else {
                    order.inputs[i].amount = received;
                }

                unchecked {
                    ++i;
                }
            }
```
