## Title
Attacker-controlled `predispatch`/postdispatch calldata executes against the shared `CallDispatcher`, letting a user plant standing ERC-20 approvals that drain funds any other order later routes through the same dispatcher - (File: `evm/src/apps/IntentGatewayV2.sol`, `evm/src/utils/CallDispatcher.sol`)

### Summary
`IntentGatewayV2.placeOrder` executes fully attacker-supplied calldata (`order.predispatch.call`) through a single, permanently shared `CallDispatcher` contract, and the equivalent postdispatch path (`IntentsBase._execute`) does the same with `order.output.call`. `CallDispatcher.dispatch` places no restriction on the call targets or data — it simply loops `to.call{value}(data)` with itself (the dispatcher) as `msg.sender` [1](#0-0) . Because the *same* dispatcher address (`_params.dispatcher`) is reused across every order placed by every user of the gateway, one attacker can use their own order's calldata to grant themselves a standing ERC-20 `approve` on the dispatcher for any token, then later pull funds out of the dispatcher whenever it legitimately (but only transiently) holds another user's tokens during that user's own predispatch/postdispatch execution window. This mirrors the `PodUnwrapLocker` root cause: an untrusted input is executed against shared, balance-holding logic with no restriction on what it is allowed to do, and the balance accounting (snapshot-before/sweep-after) implicitly trusts that nothing else touched the shared contract in between.

### Finding Description
In `placeOrder`, when `order.predispatch.call.length > 0`, the flow is:
1. Predispatch assets are transferred to `dispatcher` (`_params.dispatcher`, one address shared by the whole gateway instance) [2](#0-1) .
2. `ICallDispatcher(dispatcher).dispatch(order.predispatch.call)` executes the caller's arbitrary `Call[]` — `to`, `value`, `data` are all attacker-chosen [3](#0-2) .
3. `CallDispatcher.dispatch` only checks that `to` has code; it does not restrict the call target or payload in any way, and it executes with the dispatcher itself as the caller (not `delegatecall`, so the dispatcher's own token balances/approvals are what's at stake) [1](#0-0) .
4. The gateway then sweeps whatever `IERC20(token).balanceOf(dispatcher)` currently is for each `order.inputs[i].token` back to itself [4](#0-3) .

Nothing prevents `order.predispatch.call` from containing `Call({ to: TOKEN, value: 0, data: abi.encodeWithSelector(IERC20.approve.selector, attacker, type(uint256).max) })` for any ERC-20 the gateway/dispatcher is known to route (DAI, USDC, WETH, fee token, etc.). Since this is a real `approve` executed by the dispatcher contract, the resulting allowance is **persistent on-chain state**, independent of the placeOrder transaction's lifetime.

The same `dispatcher` is used by every other order's predispatch flow (assets are pushed to it right before `dispatch(order.predispatch.call)` runs) and by every fill's postdispatch flow (`IntentsBase._execute` pushes the fill's output through the same dispatcher before sweeping it back) [5](#0-4) . In both windows, the dispatcher briefly and legitimately holds ERC-20 balances belonging to a different user's order. An attacker holding a standing approval from step above can call `token.transferFrom(dispatcher, attacker, amount)` during that window (or any later moment the dispatcher happens to hold a non-zero balance of that token, e.g. leftover dust from a swap that produced an unlisted intermediate token that no sweep loop ever collects) and move funds to themselves. This is not a front-run of a specific victim transaction — it is a standing backdoor the attacker installs once and can trigger opportunistically any time the shared contract holds value, requiring no special relayer, prover, or admin compromise.

### Impact Explanation
This is direct, unauthorized extraction of ERC-20 balances that pass through, or accumulate as unswept dust in, the shared `CallDispatcher` — funds belonging to other users' in-flight orders or to the protocol's own dust reserve. It requires only placing one ordinary order with crafted `predispatch.call`; no relayer, prover, governance, or victim front-running assumption is needed to install the backdoor, and the backdoor stays live indefinitely until someone notices and redeploys/rotates the dispatcher.

### Likelihood Explanation
`order.predispatch.call` and `order.output.call` are fully attacker-controlled fields of the `Order`/fill path with no allow-list on call targets, and `CallDispatcher.dispatch` is generic by design ("dispatch untrusted call(s)") [6](#0-5) . The only thing standing between "generic arbitrary execution" and "fund loss" is the assumption that the dispatcher never holds meaningful balances outside a single order's atomic window — an assumption the design itself violates by using one dispatcher instance for every order and every user, and by only sweeping the specific tokens named in `order.inputs`/`order.output.assets`, not "everything the dispatcher currently holds."

### Recommendation
- Never grant a persistent, cross-order approval surface: deploy or use a per-order/per-call ephemeral executor (e.g., a minimal proxy created and destroyed within the same transaction) instead of one long-lived shared `CallDispatcher` address.
- Alternatively, have `CallDispatcher` (or the gateway) revoke/zero any approvals it granted before returning control, or restrict `Call.to` to an allow-list of known-safe routers (Uniswap, etc.) rather than arbitrary addresses.
- Sweep *all* residual balances of tokens the dispatcher could plausibly hold after each dispatch, not just the ones named in `order.inputs`/`order.output.assets`, so dust cannot accumulate as a standing target.
- Consider tightening the balance snapshot logic so `received` accounting cannot be corrupted by any state change other than the intended transfer.

### Proof of Concept
1. Attacker calls `placeOrder` with a trivial predispatch asset (e.g. 1 wei of any token) and `order.predispatch.call` encoding:
   `Call({ to: USDC, value: 0, data: approve(attacker, type(uint256).max) })`, and similarly for DAI, WETH, and the fee token.
2. `CallDispatcher.dispatch` executes this as the dispatcher, `USDC.approve(attacker, max)` succeeds and is now permanent state: `USDC.allowance(dispatcher, attacker) == max` [1](#0-0) .
3. The remainder of `placeOrder` completes normally (predispatch assets and sweep logic don't touch USDC, so nothing looks wrong on-chain for this order).
4. Later, any other user places (or fills) an order whose `predispatch`/`output` assets include USDC and routes them through the same `_params.dispatcher` (unavoidable, since it's the one configured dispatcher for the gateway) [2](#0-1) [5](#0-4) .
5. During that window (or anytime USDC dust sits unswept in the dispatcher), the attacker calls `USDC.transferFrom(dispatcher, attacker, dispatcher_balance)` using the standing approval from step 2, diverting that user's/protocol's USDC.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L203-225)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L227-227)
```text
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);
```

**File:** evm/src/apps/IntentGatewayV2.sol (L229-258)
```text
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-483)
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
```

**File:** sdk/packages/core/contracts/interfaces/ICallDispatcher.sol (L26-36)
```text
/**
 * @title The ICallDispatcher
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @notice This interface is used to dispatch untrusted call(s)
 */
interface ICallDispatcher {
    /*
     * @dev Dispatch the encoded call(s)
     */
    function dispatch(bytes memory params) external;
```
