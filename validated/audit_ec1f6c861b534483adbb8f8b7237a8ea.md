Based on my investigation, I found a concrete local analog to the "call an unverified address supplied by the caller" primitive from the Maverick Router report: `CallDispatcher.sol`, a single shared, access-control-free relay contract, is invoked with attacker-influenced `Call[]` data from multiple production apps (`IntentGatewayV2`/`IntentsBase.sol` and `HyperFungibleToken.sol`), and is documented to transiently custody token/native balances that must be "swept" back — exactly the kind of momentarily-held value an unrestricted arbitrary-call relay should never expose.

### Title
Unrestricted `CallDispatcher.dispatch()` lets anyone drain residual funds left on the shared dispatcher by order-output execution - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher` is a single, chain-wide relay contract used by both `IntentGatewayV2` (via `IntentsBase._execute`) and the `HyperFungibleToken` family to execute attacker/solver-supplied calldata (`order.output.call` / `message.data`) as part of settlement. Its `dispatch()` function is public with **no caller restriction whatsoever**, and the contract also has a `receive()` that accepts arbitrary ETH. `IntentsBase._execute` explicitly acknowledges that calls executed through the dispatcher can leave "residual balances" on it, which it sweeps back in a *second, separate* `dispatch()` call. Because `dispatch()` is unguarded, this window (or any accidental/legitimate transient balance on the shared dispatcher) is stealable by any third party who simply calls `CallDispatcher.dispatch()` first with their own `Call[]` moving that balance to themselves — mirroring the Router bug's core flaw: a fund-moving entry point trusts an unverified target/caller instead of gating it to a known, authorized registry.

### Finding Description
`IntentsBase._execute` dispatches order-fulfillment calldata to arbitrary contracts through the shared dispatcher, then performs a follow-up sweep of whatever ends up sitting on the dispatcher's balance: [1](#0-0) 

The dispatcher's `dispatch()` itself does not check `msg.sender` — anyone can call it, and it will `.call` any `to` address with any `value`/`data` it is given, as long as `to` has code: [2](#0-1) 

`HyperFungibleToken.onAccept` independently forwards attacker-controlled `message.data` to the same dispatcher pattern: [3](#0-2) 

Because the dispatcher is a shared, stateless relay with a payable `receive()` and zero access control, any ETH or ERC20 balance that momentarily lands on its address — whether from `_execute`'s own output-call step (before its second "sweep" `dispatch()` runs), from a reverted/partial sweep, or from any other app or user mistakenly sending funds to it — is available to be pulled out by any unrelated address. The dust-sweep comment in `IntentsBase.sol` confirms the dispatcher is expected to hold value transiently: [4](#0-3) 

This is the direct analog of the Router bug: just as the Router blindly called an unverified `pool` address supplied in the user's path, `IntentsBase._execute`/`HyperFungibleToken.onAccept` blindly route funds through a single shared executor contract that itself performs no verification of who may extract value that happens to be resting on it.

### Impact Explanation
Any value transiently or accidentally held by `CallDispatcher` — a chain-wide singleton shared by the Intent Gateway and the fungible-token bridge apps — can be stolen by an unprivileged external account calling `dispatch()` directly, with no relationship to the order/message that produced the balance. This is unauthorized execution and fund loss to the wrong beneficiary, matching the bounty's "stealing or loss of funds" and "unauthorized transaction or execution" categories.

### Likelihood Explanation
`_execute`'s own two-step "dispatch, then sweep" pattern in `IntentsBase.sol` demonstrates the codebase already expects the dispatcher to accumulate residual balances during normal, non-malicious solver fills of composable orders (DEX swaps, lending calls, etc. per the doc comment). Every such fill creates a window/asset on a contract with a fully public, unauthenticated `dispatch()` entry point that can be invoked by any account in the same block. No privileged actor, relayer, or governance compromise is required — an ordinary user can watch for or induce residual dispatcher balances and immediately front-run/interleave a call to `CallDispatcher.dispatch()`.

### Recommendation
Restrict `CallDispatcher.dispatch()` to a caller allow-list (e.g., only the specific app contract that owns the funds being routed, via `onlyOwner`/`onlyAuthorizedCaller` or a per-call authorization token), or make the dispatcher non-custodial by construction (e.g., have it forward `msg.value`/tokens only within the same call context and revert if any balance remains afterward, rather than relying on a later, separately-callable sweep). At minimum, ensure `_execute`'s sweep step is atomic and unconditional (no ability for value to persist on the dispatcher across transactions), and add a reentrancy/second-call guard so `dispatch()` cannot be invoked by an unrelated party while a fill's calldata is executing.

### Proof of Concept
1. A solver fills an intent order whose `order.output.call` performs a DEX swap or other multi-step DeFi interaction through `CallDispatcher.dispatch(order.output.call)` in `IntentsBase._execute` (`evm/src/apps/intentsv2/IntentsBase.sol:442`).
2. The external call(s) inside that dispatched `Call[]` leave a residual ERC20/ETH balance on the `CallDispatcher` address (acknowledged by the contract's own "DustCollected" sweep logic immediately following, lines 444-484).
3. Before `_execute`'s own sweep `dispatch()` call executes (or in any subsequent block if the sweep for some output asset is skipped/zero, or if an unrelated app such as `HyperFungibleToken` leaves dust on the same shared dispatcher instance), any external account calls `CallDispatcher.dispatch()` directly with a `Call[]` such as `{to: token, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, dispatcher.balanceOf(...))}`.
4. Since `dispatch()` performs no caller check (`evm/src/utils/CallDispatcher.sol:44-62`), the call succeeds and the attacker receives funds that belonged to the protocol/user, with no verification that the caller had any right to the dispatcher's held balance.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L294-301)
```text
    /**
     * @dev Emitted when surplus tokens are retained by the protocol. This includes
     * protocol fee deductions, surplus shares from overpayment, and residual
     * balances swept from the CallDispatcher after calldata execution.
     * @param token The token address (address(0) for native token).
     * @param amount The amount collected.
     */
    event DustCollected(address token, uint256 amount);
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

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-312)
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

        emit Received({
            from: message.from,
            to: beneficiary,
            source: string(request.source),
            amount: message.amount
        });
    }
```
