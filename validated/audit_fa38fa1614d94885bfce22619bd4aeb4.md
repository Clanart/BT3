### Title
Shared singleton `CallDispatcher` accumulates attacker-set unlimited ERC-20 approvals that persist across unrelated orders, enabling theft of other users' escrowed tokens - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`IntentGatewayV2` executes user-supplied `Call[]` arrays (predispatch and postdispatch) through a single, protocol-wide `CallDispatcher` instance stored in `_params.dispatcher` [1](#0-0) . Because this dispatcher contract is shared by every order from every user, and `dispatch()` blindly forwards arbitrary attacker-controlled calldata (including `IERC20.approve`) to arbitrary targets [2](#0-1) , any user can plant a `type(uint256).max` approval from the `CallDispatcher` to an address they control. That approval is ERC-20 contract state that lives on the token contract, keyed by `(CallDispatcher, attackerSpender)` — it has no relationship to the order that created it and is never reset. This is structurally identical to the H-5 root cause: a contract (`StopLimit`/here `CallDispatcher`) grants unlimited allowance to a party that later gains custody of unrelated funds belonging to the same contract.

### Finding Description
`placeOrder`/`fillOrder` route predispatch and postdispatch calldata through the same `dispatcher` address for all orders:
- Predispatch: assets are sent to `dispatcher`, then `ICallDispatcher(dispatcher).dispatch(order.predispatch.call)` executes attacker-supplied calls, after which only the declared `order.inputs` are swept back [3](#0-2) .
- Postdispatch: `_execute()` runs `ICallDispatcher(dispatcher).dispatch(order.output.call)` and then sweeps back **only the tokens listed in `order.output.assets`** [4](#0-3) .

Since `dispatch()` performs a raw `to.call(call.data)` with no restriction on `to` or `data` [2](#0-1) , an attacker's own order can include a call `Call({to: tokenX, data: approve(attackerSpender, type(uint256).max)})`. This executes successfully (the dispatcher is `address(this)` for that approve call, so no balance is required), and it leaves a permanent `allowance(CallDispatcher, attackerSpender) = max` on `tokenX`.

Because the dispatcher is a **singleton reused by all orders**, any subsequent, unrelated order — from any other user — that routes `tokenX` through the dispatcher (as a predispatch asset, as an intermediate swap token in postdispatch calldata not listed in `order.output.assets`, or simply as dust left over from a swap) will have that balance sitting on the dispatcher, reachable by the attacker's pre-planted allowance. The sweep logic only clears tokens explicitly declared in `order.inputs` (predispatch) or `order.output.assets` (postdispatch) [5](#0-4)  — any other token balance transiently on the dispatcher is not cleared and is fully exposed via the attacker's residual approval. The project's own documentation acknowledges the shared-custody risk in passing ("Token approvals in the Call[] should use exact amounts... since the dispatcher contract holds tokens temporarily during execution") [6](#0-5)  but this is advisory only — the code enforces nothing, and the warning addresses a caller harming *themselves*, not a malicious caller planting a standing allowance to attack *other* users of the same shared dispatcher.

The corrupted value is the ERC-20 `allowance(dispatcher, attackerSpender)` slot on any token the attacker chooses to target — set once via a self-serving order's calldata and never revoked, then leveraged against balances that arrive at the dispatcher from completely unrelated orders.

### Impact Explanation
An attacker can steal ERC-20 tokens belonging to other users/solvers that transiently pass through the shared `CallDispatcher` — e.g., predispatch input tokens of a victim's order, or intermediate/dust tokens from a victim's postdispatch swap calldata that are not part of the declared `order.output.assets` and therefore never swept. This is a direct "stealing or loss of funds" / "unauthorized transaction execution" impact against a production, permissionless entrypoint (`placeOrder`/`fillOrder`), matching the bounty's accepted impact categories.

### Likelihood Explanation
No privileged role, relayer, or governance action is required. Any unprivileged user can place an order with attacker-crafted predispatch or postdispatch `Call[]` calldata (a fully public, documented feature) to plant the malicious approval, then simply wait for a legitimate future order that routes the same token through the shared dispatcher, and call `transferFrom` from their own EOA/contract. The main uncertainty is timing/token selection (the attacker must guess or observe which tokens will later transit the dispatcher, e.g., a common intermediate swap token like WETH/USDC used broadly by solvers), which is realistic given DEX routing conventions but not guaranteed on every attempt — this is a likelihood-reducing but not likelihood-eliminating factor.

### Recommendation
Do not reuse a single global `CallDispatcher` across unrelated orders/users. Either:
1. Deploy a fresh, single-use `CallDispatcher` (e.g., via `CREATE2`/minimal proxy) per order/fill, so no approval or balance can outlive the order it belongs to, or
2. Have `CallDispatcher.dispatch` (or a wrapper around it) explicitly revoke (`approve(target, 0)`) any allowances it granted during execution before returning control, and additionally sweep *all* non-zero ERC-20 balances (not just the declared input/output token set) back to the gateway after every `dispatch()` call.

### Proof of Concept
1. Attacker (as any user) calls `placeOrder` with `order.predispatch.call` containing a single `Call`: `{to: USDC, value: 0, data: approve(attackerContract, type(uint256).max)}`, using a token/amount combination that costs the attacker nothing beyond gas (the approve call requires no dispatcher balance).
   - Reference execution path: [7](#0-6) 
2. `CallDispatcher` now has `allowance(dispatcher, attackerContract) = type(uint256).max` for USDC — this state persists indefinitely since nothing in the protocol revokes it.
3. Later, Victim places an unrelated order whose `predispatch.assets` includes USDC, or whose `postdispatch.call` swaps through USDC as an intermediate (non-output) token. USDC lands on the shared `dispatcher` address during that order's execution: [4](#0-3) .
4. Attacker's contract calls `USDC.transferFrom(dispatcher, attacker, amount)` using the standing allowance from step 2, draining USDC that belongs to the victim's order before (or instead of) the protocol's own sweep-back logic reclaims it — since sweep only covers tokens explicitly declared as `order.inputs`/`order.output.assets`, USDC left over as an untracked intermediate/dust balance is never protected.

Note: I could not fully trace the deployment/configuration path that sets `_params.dispatcher` (whether it is verifiably a single fixed address for the lifetime of the gateway with no per-order rotation) due to index size limits on `IntentsBase.sol`'s constructor/setter code; a Devin session with full repository access should confirm this configuration and validate the PoC end-to-end in Foundry before treating this as conclusively exploitable.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L383-443)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            // Transfer all predispatch assets to the call dispatcher
            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
```

**File:** evm/src/utils/CallDispatcher.sol (L44-61)
```text
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-468)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

        Call[] memory sweepCalls = new Call[](outputsLen);
        uint256 sweepCount = 0;

        for (uint256 i; i < outputsLen;) {
            address token = address(uint160(uint256(order.output.assets[i].token)));

            if (token == address(0)) {
                uint256 balance = dispatcher.balance;
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({to: address(this), value: balance, data: ""});
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            } else {
                uint256 balance = IERC20(token).balanceOf(dispatcher);
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            }
```

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L94-98)
```text
### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```
