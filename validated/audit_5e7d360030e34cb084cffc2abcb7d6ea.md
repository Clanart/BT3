# Title
Unauthenticated `CallDispatcher.dispatch` Allows Any Caller to Sweep Residual ERC20/Native Balances - (File: evm/src/utils/CallDispatcher.sol)

## Summary
`CallDispatcher.dispatch()` has no caller authentication, no module/order/chain binding, and no reentrancy protection. It is a shared, singleton, externally-callable contract used by multiple unrelated protocol modules (`IntentGatewayV2`, `IntentsBase`, `HyperFungibleToken`, `WrappedHyperFungibleToken`) as a generic "trampoline" for executing arbitrary calls on their behalf. Any address on the internet can call `dispatch()` directly and craft its own `Call[]` array to move any ERC20/native balance currently sitting on the dispatcher, regardless of which flow put it there.

## Finding Description
`dispatch` decodes an arbitrary `Call[]` and executes every element with `to.call{value: call.value}(call.data)`, gated only by `extcodesize(to) != 0`: [1](#0-0) 

There is no `msg.sender` check, no `onlyModule`/`onlyOwner` restriction, and no binding of the call to the order/commitment/module/chain that originally deposited funds into the dispatcher. The interface explicitly documents it as dispatching "untrusted call(s)": [2](#0-1) 

This single dispatcher instance is shared across multiple, independent protocol flows that transiently move ERC20/native tokens through it:
- `IntentGatewayV2` transfers `order.predispatch.assets` to the dispatcher, calls `dispatch(order.predispatch.call)`, then sweeps balances back via a second `dispatch(...)` call: [3](#0-2) 
- `IntentsBase._execute` runs `order.output.call` through the dispatcher and then sweeps any residual dust back to itself: [4](#0-3) 
- `HyperFungibleToken`/`WrappedHyperFungibleToken.onAccept` also route `message.data` through the same shared dispatcher after minting/transferring funds to the beneficiary: [5](#0-4) 

Each of these call sites relies entirely on its own atomic transaction sequencing (transfer-in → `dispatch` → sweep-out) to keep the dispatcher's balance transient. But because `dispatch()` itself enforces no caller/module/order binding, **any residual balance that is not fully swept within that same atomic transaction** — due to fee-on-transfer tokens, partial sweep failures, rounding, dust left after a failed/partial output execution, or tokens accidentally sent to the dispatcher — becomes permanently and immediately claimable by an arbitrary unprivileged caller. That caller only needs to call `CallDispatcher.dispatch()` directly with a `Call{to: token, value: 0, data: transfer(attacker, balance)}`, which is indistinguishable from a legitimate sweep call because the contract does not check who is calling or which flow/order/module the leftover balance is tied to.

This directly matches the described exploit idea: reusing a byte string/hash meant for one flow through a second, equally reachable entrypoint (`dispatch` called externally vs. `dispatch` called internally by a module) with no binding enforced at the dispatcher layer.

## Impact Explanation
Any ERC20/native token balance left on the `CallDispatcher` contract — even transiently or as dust — is not protected by any ownership, module, or order binding. An unprivileged attacker can drain it via a directly-crafted `Call[]`, corresponding to Immunefi Critical: wrongful withdrawal/redirect of protocol-controlled or user-escrowed assets, since the dispatcher is shared across `IntentGatewayV2`, `IntentsBase`, `HyperFungibleToken`, and `WrappedHyperFungibleToken`.

## Likelihood Explanation
Likelihood depends on the dispatcher ever holding a non-zero balance outside of a single atomic call sequence (e.g., fee-on-transfer tokens, sweep failing to capture 100% of balance, a user mistakenly sending tokens to the dispatcher address, or a solver's `order.output.call` not fully consuming approved tokens before the sweep enumerates `outputsLen` assets). Given the dispatcher address is publicly known/discoverable (used across several deployed apps) and `dispatch()` is a plain external function with zero access control, exploitation requires no special conditions beyond funds being present on the dispatcher at any point in time.

## Recommendation
- Restrict `CallDispatcher.dispatch` to a whitelist of authorized caller contracts (e.g., `onlyModule` mapping configurable by governance), or
- Make the dispatcher non-custodial by design: never let funds rest on it outside of a single call frame (e.g., require pre-funding via `transferFrom` with a target contract that immediately consumes it, rather than push-then-pull), or
- Deploy a fresh, single-use dispatcher (e.g., via `CREATE2` per order/message) instead of a shared singleton, so no cross-flow balance can ever accumulate on one persistent address.

## Proof of Concept
1. Any flow (e.g., `IntentGatewayV2` predispatch, or a fee-on-transfer ERC20 in `IntentsBase._execute`) leaves a small residual ERC20 balance on the shared `CallDispatcher` address after its internal sweep completes (e.g., due to imprecise dust accounting or a partially failing swept call).
2. An unrelated, unprivileged attacker observes the dispatcher's ERC20 balance via `balanceOf(dispatcher)`.
3. Attacker calls `CallDispatcher.dispatch(abi.encode([Call({to: token, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, balance)})]))` directly.
4. `dispatch` has no caller check, so the call succeeds and transfers the token balance to the attacker, regardless of which order/module/chain flow originally deposited it. [1](#0-0)

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

**File:** sdk/packages/core/contracts/interfaces/ICallDispatcher.sol (L26-37)
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-467)
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
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L320-336)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        Message memory message = abi.decode(request.body, (Message));
        address beneficiary = _toAddr(message.to);
        _mint(beneficiary, message.amount);

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }

        emit Received({from: message.from, to: beneficiary, source: string(request.source), amount: message.amount});
    }
```
