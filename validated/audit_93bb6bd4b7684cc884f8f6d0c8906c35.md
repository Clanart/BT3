## Analysis

The external report's core primitive — an unauthenticated function that forwards an attacker-controlled `(target, data)` pair into an external `call`, enabling exploitation of an ERC20 approval the intermediary contract holds — has a direct, concrete local analog in the `CallDispatcher` contract.

### The vulnerable component

`CallDispatcher.dispatch()` is `external`, has **no access control whatsoever** (no `onlyHost`, no allowlist on `msg.sender`, no restriction on `to`), and blindly forwards attacker-supplied `Call[]` entries as `to.call{value: call.value}(call.data)`: [1](#0-0) 

This single `CallDispatcher` instance is a **shared singleton** referenced by `_params.dispatcher`/`_dispatcher` across `IntentGatewayV2`/`IntentsBase` (predispatch and postdispatch execution) and across `HyperFungibleToken`/`WrappedHyperFungibleToken` (calldata execution on token receipt): [2](#0-1) [3](#0-2) 

Because `dispatch()` accepts *any* caller and *any* `Call[]`, an attacker can submit their own order (or HFT calldata payload) whose predispatch/postdispatch calls include `TOKEN.approve(ATTACKER, type(uint256).max)`. This executes with `msg.sender == CallDispatcher`, so the approval is granted **by the CallDispatcher itself** and persists on-chain indefinitely — it is not reset after the transaction.

### Why the standing approval is exploitable against other users' funds

The sweep logic that is supposed to clear residual balances off the `CallDispatcher` only accounts for the tokens explicitly listed in the order:

- `IntentsBase._execute` only sweeps `order.output.assets` tokens (`outputsLen` loop), so any token the postdispatch calls produce that isn't in that list is left permanently on the dispatcher: [4](#0-3) 
- `placeOrder`'s predispatch sweep only builds `transferCalls` for `order.inputs` tokens, leaving any other resulting token balance stuck on the same shared dispatcher: [5](#0-4) 

Since the dispatcher is one shared address used by every order and every HFT transfer, once an attacker has planted a standing `approve(ATTACKER, max)` for `TOKEN` via their own crafted order, any later balance of `TOKEN` that accumulates on the dispatcher — from an unrelated user's predispatch/postdispatch calls, fee-on-transfer remainders, or any DeFi call that leaves stray output — becomes directly drainable by the attacker simply by calling `TOKEN.transferFrom(dispatcher, attacker, balance)`. No further call to `dispatch()` is even required at that point, and no relayer, prover, or admin is involved — an ordinary user placing an ordinary order is sufficient to plant the malicious approval.

### Title
Unrestricted `CallDispatcher.dispatch()` lets an attacker plant a persistent ERC20 approval that drains funds later deposited by unrelated users - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch()` has no access control and forwards fully attacker-controlled `(to, value, data)` calls with the `CallDispatcher` itself as `msg.sender`. Because this same dispatcher instance is shared across `IntentGatewayV2`, `IntentsBase`, `HyperFungibleToken`, and `WrappedHyperFungibleToken`, an attacker can use one of these entrypoints (e.g., an order's `predispatch`/`postdispatch` calldata) to make the dispatcher grant an unlimited, persistent ERC20 `approve()` to an attacker-controlled address. That approval survives the transaction and can later be exercised against balances of the same token that are deposited on the dispatcher by any other, unrelated user.

### Finding Description
`dispatch()` decodes and executes an arbitrary `Call[]` with no restriction on caller or target: [1](#0-0) 

Both `IntentGatewayV2.placeOrder` (predispatch) and `IntentsBase._execute` (postdispatch) route user- or solver-supplied calldata through this same dispatcher, and the swept-back token set is scoped only to the order's declared `inputs`/`output.assets`, not to "whatever token balance the dispatcher happens to hold": [6](#0-5) [7](#0-6) 

An attacker's own order calldata can therefore make the dispatcher call `TOKEN.approve(attacker, type(uint256).max)`. Because the dispatcher is one shared contract instance used by every future order/transfer in the protocol (including HFT calldata paths), that standing approval remains valid for any subsequent balance of `TOKEN` the dispatcher accumulates from other, unrelated flows.

### Impact Explanation
This results in direct theft of funds belonging to other users/solvers: any token balance that transiently or permanently lands on the shared `CallDispatcher` (dust from imperfect sweeps, unlisted-token remainders from swap calldata, fee-on-transfer remainders) can be pulled out by the attacker via the standing approval, with no further interaction with `dispatch()` needed. This satisfies "stealing or loss of funds" / "unauthorized transaction" under an unprivileged-attacker threat model — no malicious relayer, prover, or admin is required.

### Likelihood Explanation
The attack requires only placing a normal order (or HFT send) with attacker-chosen predispatch/postdispatch calldata — a fully public, permissionless entrypoint. The only uncertain variable is whether/when the dispatcher subsequently holds a nonzero balance of the approved token from another party's flow, which is a function of how completely each integration's calldata sweeps its outputs (already shown to be incomplete for out-of-list tokens).

### Recommendation
- Restrict `CallDispatcher.dispatch()` to be callable only by registered/trusted caller contracts (`IntentGatewayV2`, `IntentsBase`-derived gateways, `HyperFungibleToken`/`WrappedHyperFungibleToken`), e.g. via an `onlyAuthorizedCaller` modifier.
- Disallow `approve`/`increaseAllowance`-style selectors in dispatched calls, or force the dispatcher to reset (`approve(spender, 0)`) any allowance it grants before returning.
- Sweep the dispatcher's balance of every token actually touched by a call batch (not just the tokens declared in `order.inputs`/`order.output.assets`) back to the initiating contract at the end of each `dispatch()` invocation, or make the dispatcher single-use/ephemeral per order rather than a shared singleton.

### Proof of Concept
1. Attacker calls `IntentGatewayV2.placeOrder` with `order.predispatch.call` encoding a single `Call{ to: TOKEN, value: 0, data: abi.encodeWithSelector(IERC20.approve.selector, attacker, type(uint256).max) }` and minimal/no real predispatch assets.
2. `placeOrder` forwards this to `ICallDispatcher(dispatcher).dispatch(...)`, which executes the approve with `msg.sender == dispatcher`, granting `attacker` unlimited allowance over `TOKEN` held by the dispatcher.
3. At any later time a legitimate user's order/HFT transfer causes `TOKEN` balance to sit on the same dispatcher address (e.g., postdispatch swap output not listed in `order.output.assets`, or fee-on-transfer remainder).
4. Attacker calls `TOKEN.transferFrom(dispatcher, attacker, balance)` directly, draining funds that belong to the unrelated user/solver.

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

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L316-328)
```text
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
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
