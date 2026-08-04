## Title
Permissionless `CallDispatcher.dispatch()` lets anyone plant a permanent ERC-20 approval that later drains tokens belonging to unrelated users passing through the shared dispatcher — ([File: evm/src/utils/CallDispatcher.sol])

## Summary
`CallDispatcher` is a single, shared, permissionless-execution contract reused by multiple unrelated apps (`IntentGatewayV2`'s predispatch/postdispatch flows and `HyperFungibleToken`/`WrappedHyperFungibleToken`'s calldata-execution flow). Its `dispatch()` function is callable by anyone with an arbitrary `Call[]`, executed in the dispatcher's own storage/approval context (not delegatecall, but state-mutating calls like `approve` still persist on the dispatcher's own token allowances). Any attacker can call `dispatch()` directly to make the dispatcher grant itself an unlimited ERC-20 allowance to the attacker for any token, at zero cost and with no relationship to any specific order or transfer. Because the same dispatcher instance later, legitimately, holds tokens belonging to *other* users' intents or cross-chain transfers (even momentarily, or permanently as unswept residue), the attacker's pre-planted approval lets them steal those funds in an ordinary, independent transaction — the exact "approval-farming on a shared custodial contract" bug class described in the external report, just moved from `PrivatePool`'s owner-transfer boundary to Hyperbridge's shared `CallDispatcher` singleton.

## Finding Description
`CallDispatcher.dispatch()` has no access control at all: [1](#0-0) 

Any address can call it with an ABI-encoded `Call[]` that targets any contract with any calldata and value, executed with `to.call{value: call.value}(call.data)` from the dispatcher's own address. This means an attacker can call `dispatch()` with a single `Call{ to: TOKEN, value: 0, data: approve(ATTACKER, type(uint256).max) }` and the `TOKEN` contract will record `allowance[dispatcher][ATTACKER] = max`. This approval is durable state on the token contract, not scoped to any transaction, order, or caller.

The same `CallDispatcher` instance is the one configured as `_params.dispatcher` for `IntentGatewayV2` (used in `placeOrder`'s predispatch flow and `_execute`'s postdispatch flow) and as the `dispatcher` for `HyperFungibleToken`/`WrappedHyperFungibleToken` calldata execution: [2](#0-1) 

In `IntentGatewayV2.placeOrder`, predispatch assets are transferred to the dispatcher, arbitrary calldata is dispatched, and only the tokens listed in `order.inputs` are swept back: [3](#0-2) 

Likewise `_execute` only sweeps the tokens enumerated in `order.output.assets` (`outputsLen`), not every token the postdispatch call may have touched: [2](#0-1) 

And for the fungible-token bridge, tokens are minted/unlocked directly to the dispatcher, which then executes an arbitrary `Call[]`; the docs explicitly acknowledge that the dispatcher "holds tokens temporarily during execution" and warn *senders* to use exact approvals — but that warning does nothing against an attacker who plants the malicious approval on the dispatcher itself ahead of time, unrelated to any specific sender's calls: [4](#0-3) 

Because the dispatcher is one persistent, shared contract instance across all these flows, an approval an attacker plants for token `X` in transaction 1 stays live indefinitely. Any later balance of `X` that ends up on the dispatcher — whether because a postdispatch call chain interacts with a token not enumerated in `order.output.assets`/`order.inputs` (so it's never swept), because a calldata-execution `Call[]` under-spends the minted/unlocked amount, or simply during the narrow window between a legitimate transfer landing on the dispatcher and the subsequent sweep — is drainable by the attacker via a plain `transferFrom(dispatcher, attacker, amount)` call on the token contract, completely independent of Hyperbridge, the gateway, or any order.

This is structurally identical to the `PrivatePool` finding: a low-privilege actor uses a legitimate "execute arbitrary call" entrypoint to plant a stale approval on a shared custodial contract, and that approval later lets them siphon funds that belong to a different, unrelated party once those funds transiently or permanently sit in the same contract.

## Impact Explanation
Any unrelated user's tokens that pass through, or are left as residue on, the shared `CallDispatcher` — via `IntentGatewayV2` predispatch/postdispatch flows or `HyperFungibleToken`/`WrappedHyperFungibleToken` calldata execution — can be stolen by an attacker who simply called `dispatch()` once beforehand to plant an approval. This is a direct loss-of-funds vector affecting bridge custody and intent settlement, matching the bounty's "stealing or loss of funds" and "unauthorized transaction or execution" categories, since the dispatcher's balance is not exclusively guarded per-caller/per-order.

## Likelihood Explanation
`dispatch()` is a public, unprivileged entrypoint requiring no special role, relayer, prover, or governance action — any EOA can call it directly at negligible gas cost to plant approvals for as many tokens as desired, and then simply monitor the shared dispatcher's balance for opportunities (unswept dust tokens, calldata-execution flows that don't fully drain minted balances, or narrow windows where balances sit on the dispatcher). This requires no malicious peer, relayer, or admin — only an ordinary user interacting with a public function on a shared, persistent contract.

## Recommendation
- Make `CallDispatcher` non-reusable/ephemeral per invocation (e.g., deploy a fresh minimal-proxy instance per `dispatch()` call, or have the calling app deploy/use a per-call dispatcher via `CREATE2`) so that no approval or state set during one call can be exploited against a balance held during an unrelated call.
- Alternatively, restrict `dispatch()` to only be callable by a whitelisted set of app contracts (`IntentGatewayV2`, `HyperFungibleToken`, etc.) and additionally require the dispatcher to self-check that it holds zero residual balance of every ERC-20 it touched at the end of each `dispatch()` call, reverting otherwise, so no allowance-based drain has anything to act on between calls.
- As defense in depth, have the dispatcher revoke any approval it granted at the end of every `dispatch()` invocation (loop over all `approve` calls made and reset allowances to zero) so stale approvals can never survive across transactions.

## Proof of Concept
1. Attacker calls `CallDispatcher.dispatch(abi.encode([Call{to: USDC, value: 0, data: approve(attacker, type(uint256).max)}]))` directly. This is fully permissionless per `dispatch()`'s implementation.
2. `allowance[dispatcher][attacker] = type(uint256).max` is now permanently set on the USDC contract.
3. Some time later, a legitimate, unrelated user places an `IntentGatewayV2` order whose `predispatch.call` performs a swap-then-escrow flow (e.g., ETH → USDC via Uniswap on the dispatcher) or whose `output.call` performs a postdispatch DeFi interaction that touches USDC as an intermediate token not listed in `order.inputs`/`order.output.assets`.
4. Because only tokens explicitly enumerated in `order.inputs`/`order.output.assets` are swept back to the gateway (see `_execute` and `placeOrder`'s predispatch sweep logic), any USDC balance left on the dispatcher outside that enumerated set is never swept.
5. Attacker calls `USDC.transferFrom(dispatcher, attacker, balance)` in an ordinary transaction, draining the residual balance that belongs to the unrelated user's order — funds the attacker has no legitimate claim to.

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-474)
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

```

**File:** evm/src/apps/IntentGatewayV2.sol (L203-258)
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
```

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L94-98)
```text
### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```
