## Title
`CallDispatcher.dispatch()` has no access control and can be invoked directly by anyone to spend any ETH/tokens the shared dispatcher instance is currently holding - ([File: evm/src/utils/CallDispatcher.sol])

### Summary
`CallDispatcher.dispatch(bytes memory encoded)` is a fully public, unauthenticated function that executes an arbitrary attacker-supplied `Call[]` array using the dispatcher's own balance and its own `msg.sender` identity. This is the exact bug class from the Asymmetry report: a function meant to be called only as an internal step of a controlled flow (`IntentGatewayV2.placeOrder`/`fillOrder`, `_execute`) is left publicly callable with no caller restriction, allowing anyone to trigger unauthorized spending of ETH/tokens held by the contract.

### Finding Description
`CallDispatcher` is a shared, singleton utility contract referenced by `_params.dispatcher` across multiple Hyperbridge apps (`IntentGatewayV2`, its Tron variant, and others per the docs note that "Existing `CallDispatcher` deployments are listed on the contract addresses page"). Its `dispatch` function has zero access control: [1](#0-0) 

```solidity
function dispatch(bytes memory encoded) external {
    Call[] memory calls = abi.decode(encoded, (Call[]));
    ...
    (bool success, bytes memory result) = to.call{value: call.value}(call.data);
    if (!success) revert CallFailed(to, result);
}
```

There is no `onlyGateway`/`onlyOwner`/caller check comparable to the guard the report recommended (`require caller == vEthAddress/manager`), and no reentrancy guard. The contract also has a public `receive()` that accepts ETH from anyone: [2](#0-1) 

Within the intended flow, `IntentGatewayV2` (and its Tron counterpart) push funds into the dispatcher and then immediately call `dispatch()` on it as an internal step of `placeOrder`/`fillOrder`/`_execute`: [3](#0-2) [4](#0-3) 

Because `dispatch()` is public, anyone (including a contract invoked through the attacker-controlled `order.predispatch.call` or `order.output.call` payload) can re-enter `CallDispatcher` directly and issue its own `Call[]` to move whatever balance the dispatcher currently holds to an arbitrary address, or simply call it directly to sweep any stray/dust ETH or tokens sitting on the shared dispatcher (e.g. left over from an incomplete sweep, a donation via `receive()`, or residue from a prior flow). `IntentGatewayV2.placeOrder` is `nonReentrant`, but that guard only protects re-entry into the gateway itself — it does not, and cannot, protect the separate `CallDispatcher` contract from being called directly.

### Impact Explanation
Any ETH or ERC20 tokens transiently or residually held by the shared `CallDispatcher` can be moved out by an unauthorized caller using arbitrary target/calldata, exactly analogous to the Asymmetry `VotiumStrategyCore.depositRewards()`/`AfEth.depositRewards()` pattern: a function that should only be reachable from a specific, privileged caller in a specific flow is instead open to anyone and can direct spend of contract-held value. Since the dispatcher is a single shared instance used by several apps, any accumulation of stray balance (dust, failed-sweep residue, direct `receive()` donations) becomes an open bounty for any caller, and the lack of a caller check also means the dispatcher's identity (`msg.sender` when it calls out to `to`) can be driven by anyone, not just the intended gateway contract.

### Likelihood Explanation
Exploitation requires no privileged role, relayer, or governance action — `dispatch()` is a plain `external` function with no modifier. The main constraint is that under the current call flows, funds sent to the dispatcher are normally consumed and swept back within the same atomic transaction, which limits the attack surface mostly to stray/dust balances and to reentrant redirection during the execution of attacker-supplied `order.predispatch.call`/`order.output.call` payloads. This mirrors the original report's own caveat that the equivalent Asymmetry functions were exploitable primarily against incidental balances rather than continuously-held funds — but the missing-access-control root cause is identical and directly provable in this codebase.

### Recommendation
Restrict `CallDispatcher.dispatch()` to be callable only by the authorized app contract(s) that are expected to drive it (e.g. an `onlyCaller`/allowlist mapping set at construction or via governance), and/or make the dispatcher single-use/scoped per call (e.g. deploy a minimal-proxy dispatcher per invocation, or add a reentrancy guard shared with the calling gateway) so that no unrelated party can invoke `dispatch()` outside of the flow that funded it.

### Proof of Concept
1. Any address (attacker) sends ETH directly to the shared `CallDispatcher` address via its public `receive()`, or dust/residue is left on it from a prior `IntentGatewayV2` flow that didn't fully sweep every token.
2. Attacker calls `CallDispatcher.dispatch(abi.encode(calls))` directly, with `calls = [{to: attackerControlledOrTokenContract, value: dispatcherBalance, data: transferCalldata}]`.
3. Since `dispatch()` has no caller check, the call succeeds and the dispatcher's balance is spent/transferred to the attacker's chosen target, with `msg.sender` in the outbound call being the `CallDispatcher` contract itself — the exact "unauthorized spend of contract-held resources via an unprotected function" pattern flagged in the source report.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L212-227)
```text
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-443)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

```
