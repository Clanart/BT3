## Analysis

The LiFi bug class is "any facet lets an unprivileged caller pick both the ERC20 `token` and the `spender` for an `approve` call on a contract that custodies funds it shouldn't hold." Hyperbridge's `IntentGatewayV2` has a structural analog in the `CallDispatcher` used for order `predispatch`/`output.call` execution.

`CallDispatcher.dispatch` places **no restriction on the target address or the function selector** of the calls it executes — it only checks that `to` has code: [1](#0-0) 

This dispatcher is a single, permanently-deployed, **shared singleton** (`_params.dispatcher`), reused for every user's order, in both the predispatch path of `placeOrder` and the postdispatch path of `_execute`: [2](#0-1) [3](#0-2) 

Because `order.predispatch.call` (and `order.output.call`) is fully attacker-controlled calldata executed with the dispatcher as `msg.sender`, any caller of the public `placeOrder`/`fillOrder` entrypoints can embed `IERC20(anyToken).approve(attacker, type(uint256).max)` for **any token, not just tokens involved in their own order**. That allowance is written into the target ERC20's own storage (`allowance[dispatcher][attacker]`) and is never cleared by the protocol.

The automatic sweep-back after predispatch execution only clears balances for tokens that appear in `order.inputs`: [4](#0-3) 

and the postdispatch sweep in `_execute` only clears balances for tokens listed in `order.output.assets`: [5](#0-4) 

Any token that ends up on the dispatcher outside those enumerated sets (e.g., a predispatch asset that isn't fully consumed/converted by the attacker's own calldata, or a token that legitimately transits the dispatcher briefly during another user's order) is left with a standing, attacker-held approval. The attacker can call `transferFrom` on that token at any later time to drain whatever balance the shared dispatcher is holding — funds ultimately belonging to other users passing through the same custody contract.

### Title
Unrestricted `CallDispatcher.dispatch` lets any order planter approve arbitrary spenders over the shared dispatcher's token balances - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`IntentGatewayV2.placeOrder` and `_execute` route user-supplied `predispatch.call` / `output.call` calldata through a single shared `CallDispatcher` instance (`_params.dispatcher`), used across every order from every user. `CallDispatcher.dispatch` performs an unrestricted `to.call(data)` for each attacker-supplied `Call`, with the dispatcher itself as `msg.sender`. This lets any unprivileged order placer make the dispatcher call `IERC20(token).approve(attacker, max)` for any ERC20 token, planting a persistent allowance that survives the transaction, exactly analogous to the LiFi facets' user-controlled `approve(router, amount)` bug.

### Finding Description
`placeOrder`'s predispatch path transfers `order.predispatch.assets` to `dispatcher`, calls `ICallDispatcher(dispatcher).dispatch(order.predispatch.call)` with attacker-chosen `Call[]`, and only afterward sweeps back balances for tokens present in `order.inputs`. Because `dispatch` does not whitelist targets or selectors, the attacker's `Call[]` can target any ERC20 and invoke `approve(attacker, type(uint256).max)`. This grants the attacker a standing allowance over whatever balance of that token the dispatcher holds at any point — now or in the future, from any user's order — since the dispatcher is a single shared, long-lived contract (`_params.dispatcher`), not a fresh, isolated contract per order.

The sweep logic never revokes approvals, and it only clears the specific tokens enumerated in `order.inputs` (predispatch) or `order.output.assets` (postdispatch). Any other token balance left on the dispatcher — including dust from an incompletely-consumed predispatch swap, or a token in transit during another user's simultaneous order — is directly exposed to the previously planted allowance.

### Impact Explanation
An attacker can steal ERC20 tokens custodied by the shared `CallDispatcher` that belong to other users, by planting an unlimited approval via crafted `predispatch.call`/`output.call` data in a single `placeOrder`/`fillOrder` call, then later calling `transferFrom` on the token contract directly to drain any balance the dispatcher subsequently accumulates for that token. This is unauthorized fund extraction from a protocol-operated custody contract, matching the bounty's "stealing or loss of funds" impact category.

### Likelihood Explanation
`placeOrder` and `fillOrder` are public, unprivileged entrypoints; no peer, relayer, prover, or admin compromise is required — a single malicious order is sufficient to plant the approval. Exploitation of the planted allowance requires the dispatcher to actually hold a balance of the approved token at some later point (e.g., unswept predispatch dust, or a race with another user's order), so realized theft is opportunistic/dust-dependent rather than immediate, but the root cause — unrestricted arbitrary calls from a shared custody contract — is deterministic and directly reachable by any user today.

### Recommendation
Restrict `CallDispatcher.dispatch` (or a variant used for predispatch/output execution) to disallow calls whose selector is `approve`/`increaseAllowance`/`setApprovalForAll` on ERC20/ERC721 targets, or better, deploy a fresh, single-use `CallDispatcher` per order (e.g., via `CREATE2` with the commitment as salt) so no allowance or balance can ever persist or be shared across orders/users. Additionally, ensure predispatch sweeping clears *all* non-zero balances on the dispatcher for any token touched by `predispatch.assets`, not only tokens present in `order.inputs`, and emit `DustCollected` for the remainder as already done in `_execute`.

### Proof of Concept
1. Attacker calls `placeOrder` with:
   - `order.predispatch.assets = [{token: DAI, amount: X}]`
   - `order.inputs = [{token: OUTPUT_TOKEN, amount: Y}]` (different token from DAI)
   - `order.predispatch.call` encoding a `Call[]` where one call is `DAI.approve(attacker, type(uint256).max)` (executed by the dispatcher, so `msg.sender == dispatcher` inside DAI), and another call converts only part of the DAI to `OUTPUT_TOKEN` (or none at all, reverting only the missing-balance sweep check for `OUTPUT_TOKEN`, which can be satisfied by directly funding it in the same calldata).
2. Transaction succeeds; `DAI.allowance(dispatcher, attacker)` is now `type(uint256).max`, and any leftover DAI balance on `dispatcher` is never swept (only `OUTPUT_TOKEN` is swept back per `order.inputs`).
3. At any later time — including when another user's `placeOrder`/`fillOrder` predispatch/postdispatch step also uses DAI and transiently or residually leaves DAI on the shared `dispatcher` — attacker calls `DAI.transferFrom(dispatcher, attacker, balance)` directly, stealing those funds. [1](#0-0) [6](#0-5)

### Citations

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

**File:** evm/src/apps/IntentGatewayV2.sol (L203-280)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;
                if (amount == 0) revert InvalidInput();

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-443)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L444-485)
```text
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
