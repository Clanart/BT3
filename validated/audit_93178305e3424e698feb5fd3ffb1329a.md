## Analysis

The external report's core primitive is: a privileged "callback" mechanism accepts an **attacker-controlled target + calldata**, and the callback executor has **no restriction on who may invoke it or what balance it acts upon**, letting an unprivileged caller redirect funds that should only move through the intended, escrow-checked path.

The direct local analog is `CallDispatcher.sol`, a **shared, singleton, zero-access-control contract** used by `IntentGatewayV2`, `IntentsBase`/`IntrinsicIntents`/`ExtrinsicIntents`, `HyperFungibleToken`, and `WrappedHyperFungibleToken` to execute solver/user-supplied calldata during order fills and cross-chain token transfers.

### Title
Unrestricted `CallDispatcher.dispatch()` allows anyone to drain unswept token/ETH dust left on the shared dispatcher - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch(bytes)` has no caller restriction whatsoever — no `onlyX` modifier, no ownership check, nothing beyond "target must be a contract." It is a shared singleton address (`_params.dispatcher`) reused across `IntentGatewayV2.placeOrder`/`fillOrder` (predispatch/postdispatch legs), `IntentsBase._execute`, `HyperFungibleToken.onAccept`, and `WrappedHyperFungibleToken.onAccept`. Each of these callers temporarily parks tokens/ETH on the dispatcher, invokes `dispatch()` with solver- or message-supplied `Call[]`, and then sweeps back only a caller-enumerated, fixed set of tokens (`order.output.assets` / `order.inputs`). Any token balance that lands on the dispatcher outside that enumerated set (e.g. a reward/bonus token returned by a DEX swap invoked through solver-controlled `order.output.call`, or residual dust from a partially-consumed predispatch swap) is never swept and persists on the dispatcher's own balance. Because `dispatch()` is public and unauthenticated, **any external account** can call `CallDispatcher.dispatch()` directly at any time with a `Call[]` that moves that residual balance to itself. [1](#0-0) 

### Finding Description
`dispatch()` decodes an arbitrary `Call[]` and executes each entry with `to.call{value: call.value}(call.data)`, restricted only by `extcodesize(to) != 0`: [1](#0-0) 

There is no check that `msg.sender` is one of the authorized apps (`IntentGatewayV2`, `IntentsBase`, `HyperFungibleToken`, etc.). Compare this to every other privileged entrypoint in the codebase, which is gated with `restrict(...)` / `onlyHost` (e.g. `HostManager.onAccept` at `evm/src/core/HostManager.sol:95`, `EvmHost.dispatchIncoming` at `evm/src/core/EvmHost.sol:794`). `CallDispatcher` is the one execution surface in the intent/token-bridging stack with no such gate.

The sweep-back logic that is supposed to reclaim any balance left on the dispatcher after executing solver/user calldata only iterates over the caller-declared token list, not the dispatcher's actual balance set: [2](#0-1) 

Similarly in `IntentGatewayV2.placeOrder`'s predispatch flow, only `order.inputs` tokens are swept back after the predispatch call executes: [3](#0-2) 

Because `order.output.call` (in `_execute`) and `order.predispatch.call` (in `placeOrder`) are attacker/solver-controlled calldata routed through DEXes, lending protocols, "or other DeFi primitives" (per the docstring), it is entirely plausible for a solver-chosen swap route to yield a bonus/reward token, or for fee-on-transfer/rounding behavior to leave a non-enumerated token balance on the dispatcher. Since `dispatch()` itself is unauthenticated, the corrupted value that any address can act on is: **whatever ERC-20 balance or native ETH balance currently sits at the `CallDispatcher` contract address**, regardless of which app or order that balance nominally belongs to.

### Impact Explanation
This breaks "bridged assets, order escrow, refunds ... must move exactly once and only to the rightful beneficiary and amount." Funds that should be swept back to the `IntentGateway`/`IntentsBase` contract (and ultimately to the order's beneficiary or protocol dust accounting) can instead be siphoned by any unprivileged third party who simply calls `CallDispatcher.dispatch()` directly, since the contract enforces no caller identity and the enumerated sweep in the calling apps cannot claim tokens it doesn't know about. This is unauthorized execution / wrong-beneficiary fund loss on a production bridge contract, reachable from a fully public entrypoint.

### Likelihood Explanation
No malicious relayer, prover, or governance actor is required — `CallDispatcher.dispatch()` is `external` with zero modifiers, callable by any EOA or contract. The only precondition is that the shared dispatcher holds a non-zero, non-enumerated balance at some point, which is plausible any time solver- or user-controlled calldata is routed through external DeFi protocols (swaps, LPs) that can return unexpected token types or leave rounding dust, as explicitly anticipated by the composability design (`_execute` docstring: "solvers can route through DEXes, lending protocols, or other DeFi primitives").

### Recommendation
Restrict `CallDispatcher.dispatch()` to an authorized set of callers (e.g. a `restrict`/allow-list check against the registered `IntentGatewayV2`, `IntentsBase`, `HyperFungibleToken`, `WrappedHyperFungibleToken` instances), mirroring the `onlyHost`/`restrict` pattern used elsewhere in the codebase. Additionally, have the calling contracts sweep the dispatcher's *actual* balance for any token that could plausibly appear (or maintain a strict allow-list of tokens the dispatcher is permitted to hold/interact with) rather than only the caller-declared `order.inputs`/`order.output.assets` sets.

### Proof of Concept
1. A solver fills an order via `IntentGatewayV2.fillOrder`/`IntentsBase._execute` whose `order.output.call` routes through a third-party swap that returns a bonus/reward token not present in `order.output.assets`, or leaves rounding dust of some token X on the `CallDispatcher` address.
2. `_execute` (`evm/src/apps/intentsv2/IntentsBase.sol:438-485`) only enumerates and sweeps `order.output.assets[0..outputsLen)`; token X's balance on the dispatcher is left untouched after the transaction completes.
3. An unrelated attacker calls `CallDispatcher.dispatch(abi.encode([Call({to: X, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, IERC20(X).balanceOf(dispatcher))})]))` directly — this call requires no permission and succeeds because `dispatch()` has no caller check (`evm/src/utils/CallDispatcher.sol:44-62`).
4. Token X, which should have accrued to the protocol/beneficiary as dust, is now transferred to the attacker.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L44-62)
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
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-485)
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

            unchecked {
                ++i;
            }
        }

        if (sweepCount > 0) {
            Call[] memory finalCalls = new Call[](sweepCount);
            for (uint256 i; i < sweepCount;) {
                finalCalls[i] = sweepCalls[i];
                unchecked {
                    ++i;
                }
            }
            ICallDispatcher(dispatcher).dispatch(abi.encode(finalCalls));
        }
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L227-280)
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
