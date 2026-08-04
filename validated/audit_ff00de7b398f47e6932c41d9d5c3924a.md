## Title
Unrestricted `CallDispatcher.dispatch()` lets anyone drain any native ETH stranded in the shared dispatcher — ([File: evm/src/utils/CallDispatcher.sol])

### Summary
`CallDispatcher` is the single, persistent, shared contract that `IntentGatewayV2`/`IntentsBase` route escrowed native ETH and ERC20 through during `placeOrder`'s predispatch step and `fillOrder`'s output-execution step. Its `dispatch()` entrypoint has **no access control** and its `receive()` accepts ETH from anyone, so any unprivileged address can call `dispatch()` directly and forward the dispatcher's entire native balance to an arbitrary address. This is the same underlying primitive as the Radiant `RevenueManagement`/`DexSwapStrategy` report: an arbitrary-call executor that can hold and forward native value with no invariant tying the value moved to who is entitled to it.

### Finding Description
`CallDispatcher.dispatch()` executes attacker-supplied `Call[]` (arbitrary `to`, `value`, `data`) with no caller restriction: [1](#0-0) 

It also has an unrestricted `receive()`: [2](#0-1) 

Crucially, `_params.dispatcher` is one fixed, shared address used by every `placeOrder`/`fillOrder` call across all users, set once at `initialize` and reused by all subsequent calls: [3](#0-2) 

During `placeOrder`, native ETH for `predispatch.assets` is sent to this shared dispatcher, an arbitrary `predispatch.call` is executed against it, and only afterward is the balance swept back based on the *known* `order.inputs` tokens: [4](#0-3) 

Similarly, in `_execute` (used by `fillOrder`), the solver's arbitrary `order.output.call` is dispatched, and the sweep-back loop only iterates over the tokens explicitly listed in `order.output.assets`: [5](#0-4) 

If the solver-supplied `output.call` (or a predispatch `call`) leaves native ETH on the dispatcher that isn't accounted for by the enumerated output/input token list (e.g., a DEX interaction that leaves ETH change, a partially-failed multi-step call, or ETH sent directly to the dispatcher's permissionless `receive()`), that ETH is not swept and remains sitting in the shared `CallDispatcher` after the transaction completes. Because `dispatch()` has no restriction to only the gateway/host, **any unprivileged actor** can immediately call `ICallDispatcher(dispatcher).dispatch(...)` in a separate transaction with `Call({to: attacker, value: dispatcher.balance, data: ""})` and steal it — exactly the "arbitrary call receives ETH and sends it to a different address" pattern from the seed report, except here the executor itself (`CallDispatcher`) is the unguarded public entrypoint rather than a downstream strategy contract.

### Impact Explanation
This causes direct loss of funds: native ETH belonging to a user/solver (via dust, stranded change from an output/predispatch call, or misdirected sends) can be permanently and unauthorizedly extracted by any third party, since `CallDispatcher.dispatch()` performs no sender check and no invariant ties the value forwarded to a legitimate order/beneficiary. This matches the required impact class of unauthorized execution / stealing of funds via a public, unprivileged entrypoint — no relayer, prover, or admin compromise is needed.

### Likelihood Explanation
Exploitation requires no privileged role: the attacker only needs to observe (via mempool/explorer) that `CallDispatcher` holds a nonzero native balance and immediately call its public `dispatch()` function. Because the dispatcher is a single shared, long-lived contract referenced by `_params.dispatcher` across all orders, any ETH left behind by any order's predispatch/output call flow (accidental leftover, rounding, or a call that doesn't perfectly zero out its ETH balance) is a standing bounty for the first caller of `dispatch()`, not the rightful order participant.

### Recommendation
Restrict `CallDispatcher.dispatch()` to only be callable by the authorized gateway/host contract(s) (e.g., an `onlyAuthorized` modifier checking `msg.sender` against a registered gateway address), or make the dispatcher ephemeral (deployed fresh per order/call and self-destructed/never reused) so no cross-transaction balance can persist for an unrelated party to claim. Additionally, ensure sweep logic in `IntentGatewayV2.placeOrder` and `IntentsBase._execute` accounts for and reclaims *all* native ETH left on the dispatcher (not only amounts tied to enumerated `order.inputs`/`order.output.assets` tokens) before the transaction completes.

### Proof of Concept
1. Any transaction causes the shared `CallDispatcher` (`_params.dispatcher`) to end up holding a nonzero native ETH balance after execution — e.g., a solver's `output.call` in `fillOrder` performs a swap that returns unspent ETH to the dispatcher for a token not listed in `order.output.assets`, so `_execute`'s sweep loop never queries/collects it.
2. An unrelated attacker, observing `address(dispatcher).balance > 0` on-chain, calls `ICallDispatcher(dispatcher).dispatch(abi.encode([Call({to: attackerAddr, value: dispatcher.balance, data: ""})]))` directly — this succeeds because `dispatch()` has no caller restriction.
3. The attacker receives the stranded ETH that rightfully belonged to the order's user/solver, with no possibility for the legitimate party to reclaim it.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L104-115)
```text
    function initialize(Params memory p, bytes[] memory peerChains) public initializer {
        uint256 peersLength = peerChains.length;
        for (uint256 i = 0; i < peersLength; i++) {
            Deployment memory deployment = Deployment({
                chain: peerChains[i],
                gateway: address(this)
            });
            _addDeployment(deployment);
        }
        _validateParams(p);
        _params = p;
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L202-258)
```text
        uint256 msgValue = msg.value;
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
