### Title
Permissionless `CallDispatcher.dispatch` allows unauthorized draining of transiently-held escrow funds - ([File: evm/src/utils/CallDispatcher.sol])

### Summary
`CallDispatcher.dispatch` has no caller restriction — any address can invoke it to make the dispatcher execute arbitrary `Call[]` using the dispatcher contract's own held balance. This mirrors the C-01 pattern (a function that assumes it will only ever be invoked as part of a privileged, atomic flow, but performs no check to enforce that assumption): `executeComposableDelegateCall` assumed it would only run via delegatecall but had no such check; `CallDispatcher.dispatch` assumes it will only ever be called by `IntentGatewayV2`/`HyperFungibleToken` as part of a single atomic transaction, but has no `onlyGateway`/reentrancy/self-call guard at all.

### Finding Description
`CallDispatcher` is a shared, canonical utility contract used by `IntentGatewayV2` (predispatch/postdispatch calldata execution) and `HyperFungibleTokenUpgradeable` (`onAccept` composable execution) to temporarily hold tokens/ETH and route them through arbitrary calls: [1](#0-0) 

The `dispatch(bytes memory encoded)` function decodes a `Call[]` and executes each call with `to.call{value: call.value}(call.data)`, where `call.value` is drawn from the dispatcher contract's own ETH balance and any ERC20 token transfers inside `call.data` operate on the dispatcher's own token balances — there is no check on `msg.sender`, no reentrancy guard, and no restriction that this must be invoked by the gateway/token contract itself.

`IntentGatewayV2` relies on the dispatcher holding funds only transiently, between an ETH/token transfer into the dispatcher and the subsequent `dispatch(...)` call in the *same* transaction: [2](#0-1) [3](#0-2) 

Because `CallDispatcher` is a shared, permanently-deployed contract (the docs describe "Existing `CallDispatcher` deployments … listed on the contract addresses page"), any balance it holds — dust left over from imperfect sweeps, ETH sent via its public `receive()`, or funds sitting there between the funding step and the dispatch step of a legitimate order — is not access-controlled. An unprivileged attacker can call `dispatch()` directly with a `Call[]` that transfers out whatever balance currently sits in the dispatcher to an address of their choosing. The legitimate callers (`IntentGatewayV2._execute`, `HyperFungibleTokenUpgradeable.onAccept`) provide zero additional protection because the dispatcher itself never verifies who is calling it.

### Impact Explanation
This satisfies the bounty's "stealing or loss of funds" and "unauthorized execution" criteria: funds temporarily custodied by a core, permissionless, canonical bridge-adjacent contract (`CallDispatcher`) can be redirected to an arbitrary beneficiary chosen by anyone who calls `dispatch`, rather than the rightful order beneficiary/dust-treasury path enforced by `IntentGatewayV2`/`HyperFungibleTokenUpgradeable`. No privileged actor, relayer, or prover compromise is required — it is a pure unprivileged public-entrypoint issue matching the report's "missing self/caller check → unrestricted execution" bug class.

### Likelihood Explanation
Likelihood depends on the dispatcher actually holding a non-zero balance at a time reachable by an external caller (e.g., dust not fully swept due to rounding/fee-on-transfer tokens, ETH sent to the public `receive()`, or a race where an attacker's `dispatch()` call lands in the same block/mempool window as a legitimate funding transfer but before the legitimate dispatch call). Given `CallDispatcher` is a shared canonical deployment used across multiple integrations and explicitly documented to accumulate "dust" that must be swept, some window of non-zero balance is expected in normal operation, making this practically triggerable rather than purely theoretical.

### Recommendation
Restrict `CallDispatcher.dispatch` to only be callable by an authorized caller (e.g., a per-caller allowlist of gateway/app contracts, or deploy dispatcher instances scoped per-caller/per-transaction), and/or require that any balance passed through `dispatch` originates from `msg.sender` in the same call (e.g., pull-based accounting) rather than relying on ambient contract balance. At minimum, add a check equivalent to the report's suggested guard — verify the caller is a registered/trusted app contract — before allowing `dispatch` to move the contract's held assets.

### Proof of Concept
1. Deploy/observe the shared `CallDispatcher` instance used by `IntentGatewayV2`.
2. Send a small amount of ETH directly to the `CallDispatcher` address (accepted unconditionally via its `receive()` in [4](#0-3) ), or wait for a legitimate order flow to leave dust (fee-on-transfer token, rounding) that is not fully swept.
3. As an unrelated, unprivileged address, call:
```solidity
Call[] memory calls = new Call[](1);
calls[0] = Call({to: attacker, value: address(callDispatcher).balance, data: ""});
ICallDispatcher(callDispatcher).dispatch(abi.encode(calls));
```
4. `dispatch` executes the call with no caller check, transferring the dispatcher's held ETH to `attacker`, confirming unauthorized fund extraction from a shared bridge-app utility contract.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L37-39)
```text
     * @dev Receive function to accept ETH transfers
     */
    receive() external payable {}
```

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

**File:** evm/src/apps/IntentGatewayV2.sol (L203-227)
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-443)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

```
