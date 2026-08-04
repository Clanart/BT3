### Title
Unrestricted `CallDispatcher.dispatch()` lets anyone steal ETH/tokens left resting in the shared dispatcher during order execution - ([File: evm/src/utils/CallDispatcher.sol])

### Summary
`CallDispatcher` is the shared, singleton contract that `IntentGatewayV2` routes all order `predispatch`/`postdispatch` calldata through (DEX swaps, multi-hop routing, etc.). It accepts ETH via `receive()` and exposes `dispatch(bytes)` with **no access control whatsoever** — not `onlyGateway`, not `onlyOwner`, nothing. Any balance (native or ERC-20) that is momentarily or permanently resident in `CallDispatcher` between order-execution and the gateway's dust-sweep can be redirected to an attacker-chosen address by simply calling `dispatch()` directly, bypassing `IntentGatewayV2` entirely.

### Finding Description
`IntentsBase._execute()` routes calldata execution through the dispatcher and then attempts to sweep back only the balances of tokens that are explicitly listed in `order.output.assets`: [1](#0-0) 

Any token or native ETH that ends up on the dispatcher but is **not** one of the order's declared output assets (e.g. an intermediate hop token from a multi-step swap route, unspent gas refund, or a leftover balance from a call that doesn't consume its full `value`) is never swept back to the gateway and is left sitting in `CallDispatcher`.

Critically, `CallDispatcher.dispatch()` itself is completely public: [2](#0-1) 

There is no restriction requiring the caller to be `IntentGatewayV2`, no reentrancy/ownership check, and no accounting of "whose" funds are held by the contract. Because `dispatch()` accepts an arbitrary `Call[]` and forwards `call.value` to any `to` address with code, anyone who observes a non-zero balance on `CallDispatcher` (native or ERC-20, since ERC-20 transfers are just arbitrary calls too) can call `dispatch()` directly with `Call({to: attackerContract, value: balance, data: ""})` (or an ERC20 `transfer` call) and drain it to themselves.

This differs from — and is more severe than — the reported M-06 pattern (a payable function with no withdraw path locking funds): here the resting funds are not merely inaccessible, they are actively stealable by an unprivileged attacker due to the total absence of caller authentication on the fund-moving entrypoint.

### Impact Explanation
This directly satisfies the "stealing or loss of funds" / "unauthorized transaction or execution" bounty gate. Any dust, slippage residue, or transient balance from composable order fulfillment (DEX swaps, multi-hop calldata, partial executions) that is not perfectly captured by the narrow output-asset-only sweep logic in `_execute` becomes permanently exposed to theft by any third party, with no privileged role, relayer, or prover assumption required.

### Likelihood Explanation
Medium-High. `CallDispatcher` is explicitly designed to be used for composable, multi-step DeFi routing (per `docs/content/developers/evm/intent-gateway/overview.mdx`), which routinely produces intermediate-token or rounding dust that does not match the order's declared `output.assets` set exactly. Any solver/user calldata path that leaves such dust creates an exploitable window; no attacker collusion with a relayer/prover/admin is needed — the theft path is a plain unauthenticated external call.

### Recommendation
- Restrict `CallDispatcher.dispatch()` to only be callable by the registered `IntentGatewayV2` instance(s) (e.g. an `onlyAuthorizedCaller` modifier keyed off `_params.dispatcher`'s configured caller), or
- Make `CallDispatcher` non-custodial by design: forward all `value`/token balances atomically within the same call rather than allowing them to rest, and add a governance-gated sweep function scoped to the deploying gateway, removing the open, permissionless `dispatch()` surface for moving resting balances.

### Proof of Concept
1. A user places a cross-chain order whose `output.call` routes through a DEX aggregator via `CallDispatcher.dispatch(order.output.call)`, e.g. swapping token A → token B → token C where only token C is declared in `order.output.assets`.
2. Due to slippage/rounding, a small amount of intermediate token B (or leftover native ETH) remains on `CallDispatcher` after the swap completes.
3. `_execute`'s sweep loop only checks balances for tokens in `order.output.assets` (token C), so the token B / ETH dust is left on `CallDispatcher`.
4. An attacker (unrelated to the order, no special role required) calls `CallDispatcher.dispatch(abi.encode([Call({to: attackerReceiver, value: <ETH balance>, data: ""})]))` directly, or a matching ERC-20 `transfer` call for token B, draining the dust to their own address.
5. The funds are permanently lost from the protocol's perspective and stolen by the attacker, with no gateway/governance/relayer involvement.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-473)
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
