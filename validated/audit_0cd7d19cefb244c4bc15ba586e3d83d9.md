### Title
Balance-based (not delta-based) sweep from the shared, permissionless `CallDispatcher` lets an unprivileged caller appropriate stray/residual funds into their own order's escrow - `IntentGatewayV2.placeOrder` (`evm/src/apps/IntentGatewayV2.sol`, `evm/src/utils/CallDispatcher.sol`)

### Summary
The external report's core broken invariant is: *a contract trusts a freely-manipulable balance reading (`address(this).balance`) as proof of a specific operation's outcome, instead of tracking that outcome through internal, operation-scoped accounting*. The local analog is `IntentGatewayV2.placeOrder`'s predispatch "sweep," which treats the entire current balance of a shared, permissionless `CallDispatcher` contract as the output of the *current* order's `predispatch.call`, and credits that whole balance toward the caller's `order.inputs[i].amount` (the value that becomes the order's on-chain escrow/commitment).

### Finding Description
`CallDispatcher` is a single shared, non-order-scoped contract with an unrestricted `receive()` and no caller allowlist on `dispatch`: [1](#0-0) 

During `placeOrder`'s predispatch path, the gateway executes the caller-supplied `order.predispatch.call` on the dispatcher, then reads the dispatcher's **total live balance** — not a delta scoped to that call — and treats it as the amount available to sweep for the current order: [2](#0-1) 

```
uint256 balance = address(dispatcher).balance;
if (balance < requiredAmount) revert InsufficientNativeToken();
transferCalls[i] = Call({to: address(this), value: balance, data: ""});
```
and the ERC20 equivalent using `IERC20(token).balanceOf(dispatcher)`. The check `balance < requiredAmount` only validates that *some* funds sit in the dispatcher at that moment — it does not verify those funds were produced by the caller's own `predispatch.call`/`predispatch.assets`. Any ETH or ERC20 tokens already resting in `dispatcher` (from a stray/mistaken transfer, from dust of a token *not* listed in a prior order's `inputs[]` — which is never swept back since only tokens explicitly enumerated in `order.inputs` are iterated — or from anyone directly calling `receive()`) is indistinguishable from output actually produced by the current caller's predispatch call.

The swept amount then flows directly into the order's escrow and commitment: [3](#0-2) [4](#0-3) 

If the swept total exceeds `order.inputs[i].amount`, the excess is only emitted as `DustCollected` (kept by the protocol) rather than reverted or returned to whoever actually owned it; if the swept total is at or below the required amount, it is silently accepted (`order.inputs[i].amount = received`) with no requirement that the caller's own predispatch call was the source. This exactly mirrors the GenesisGroup pattern: a state-changing decision (how much value this operation escrows/commits) is gated on a coarse, externally-influenceable balance reading of a shared contract instead of an internal, operation-scoped accounting value.

### Impact Explanation
An unprivileged caller can construct a `placeOrder` transaction with a trivial/no-op `predispatch.call` (satisfying the `success` requirement in `CallDispatcher.dispatch` without producing any real output) and `order.inputs[i].amount` set to match whatever balance is already sitting in the shared `dispatcher` for a given token/native asset. The sweep will pull that pre-existing balance into the caller's own order, satisfying the `requiredAmount` check and crediting the caller's order with escrow they never funded through this operation. This is a direct fund-diversion primitive against whichever party's funds ended up resting in the shared dispatcher (stray sends, or un-enumerated surplus output tokens from a prior order's arbitrary predispatch call that were never listed in that order's `inputs[]` and thus never swept back). Because the escrowed `order.inputs[i].amount` is exactly what a solver must later match/settle via `fillOrder`, the order books an escrow of real value the placer did not actually contribute in this transaction — a wrong-attribution / fund-diversion outcome that fits the bounty's "unauthorized transaction/execution" and "logic attack" categories.

### Likelihood Explanation
No relayer, prover, admin, or governance actor is required — the entire path is reachable by any ordinary user calling the public `placeOrder` entrypoint with an order specifying `predispatch.assets`/`predispatch.call`, both fully attacker-controlled fields. The precondition (non-zero residual balance sitting in the shared `dispatcher`) can be self-created by the attacker (a plain ETH/token transfer to the dispatcher address requires no privilege, since `receive()` is unguarded and `dispatch` has no caller allowlist) or opportunistically harvested whenever it naturally accumulates from other orders' un-enumerated predispatch outputs.

### Recommendation
Do not use the dispatcher's absolute balance as the sweep amount. Instead, snapshot the dispatcher's balance for each relevant token *before* dispatching `order.predispatch.call`, and sweep only the **delta** produced by that specific call (`balanceAfterCall - balanceBeforeCall`), reverting if the delta is below `requiredAmount`. This removes any dependence on pre-existing/stray dispatcher balance, closing the same class of "balance instead of internal delta accounting" bug the external report describes for GenesisGroup. Additionally, consider making `dispatch` restricted to being called only by the configured gateway (or otherwise ensuring the dispatcher cannot retain cross-transaction balance) so it cannot function as a shared piggy bank across unrelated orders.

### Proof of Concept
1. Attacker sends 1 ETH directly to the gateway's configured `dispatcher` address (a plain external transfer succeeds because of the unguarded `receive()` in `CallDispatcher`) — see `evm/src/utils/CallDispatcher.sol:36-39`. This can also arise "for free" as residual dust from an earlier legitimate order whose `predispatch.call` produced a token not listed in that order's `inputs[]` (never swept back by `evm/src/apps/IntentGatewayV2.sol:232-256`, which only iterates tokens explicitly present in `order.inputs`).
2. Attacker calls `placeOrder` with:
   - `order.predispatch.assets = [{token: address(0), amount: 1 wei}]` (satisfies `amount == 0` check) and a `predispatch.call` targeting any contract that trivially accepts the call and does nothing productive with the funds (must merely not revert, per `CallDispatcher.dispatch`'s `success` check).
   - `order.inputs = [{token: address(0), amount: 1 ether}]`.
3. During execution, `evm/src/apps/IntentGatewayV2.sol:238` reads `balance = address(dispatcher).balance` = 1 ether (the attacker's pre-funded stray ETH, untouched by the no-op predispatch call), passes the `balance < requiredAmount` check, and the entire 1 ether is swept to the gateway (`evm/src/apps/IntentGatewayV2.sol:240-241`).
4. `received == order.inputs[0].amount` exactly, so `order.inputs[0].amount` is accepted as-is (`evm/src/apps/IntentGatewayV2.sol:270-275`) and 1 ether of escrow is credited to the attacker's order commitment (`evm/src/apps/IntentGatewayV2.sol:333-343`) — funds the attacker never actually contributed through a genuine predispatch operation, using only a permissionless direct transfer to the shared dispatcher as the "source."

Note: I was not able to fully trace every code path that could leave *legitimate, non-attacker-created* residual balances in the dispatcher across production usage (e.g., exact conditions under which real predispatch swaps might strand un-enumerated surplus tokens there) within the available search depth; the core mechanism (balance, not delta, sweep from a permissionless shared contract) is confirmed directly from the cited code.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L36-62)
```text
    /**
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}

    /**
     *  @dev reverts if the target is not a contract or if any of the calls reverts.
     */
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L227-256)
```text
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
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

**File:** evm/src/apps/IntentGatewayV2.sol (L260-280)
```text
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

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```
