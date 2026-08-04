## Title
Unauthenticated `CallDispatcher.dispatch()` Allows Anyone to Drain Residual Funds Left by Intent/HFT Swap Flows - (File: `evm/src/utils/CallDispatcher.sol`)

## Summary
`CallDispatcher` is deployed once and shared as a persistent, long-lived contract across `IntentGatewayV2` (predispatch/postdispatch swap execution), `HyperFungibleTokenUpgradeable`, and `WrappedHyperFungibleToken` — all of which temporarily route ERC20/native token balances through it before "sweeping" only a fixed, pre-declared set of token addresses back to the calling app. `CallDispatcher.dispatch()` itself has **no access control whatsoever** — it is `external` with no `onlyOwner`/`onlyGateway` modifier and no `msg.sender` check [1](#0-0) . This exactly mirrors the reported `ProxyCall` bug class: an unguarded execution primitive combined with an incomplete balance sweep, which lets any address drain leftover funds sitting in the shared contract.

## Finding Description
`IntentsBase._execute()` dispatches solver-supplied swap calldata through the shared `CallDispatcher`, then sweeps back only the tokens listed in `order.output.assets[0..outputsLen)` [2](#0-1) . Similarly, `IntentGatewayV2.placeOrder()`'s predispatch flow sweeps back only the tokens listed in `order.inputs` after executing `order.predispatch.call` [3](#0-2) .

Both sweep routines are scoped to a fixed, caller-declared token list, not to "whatever balance the dispatcher actually holds." Any token that:
- is an intermediate hop in a multi-hop DEX route used by the solver's calldata but isn't one of the declared input/output tokens, or
- is left behind due to a partial-use swap (the same "DEX only consumes part of tokenIn" scenario from the original report), or
- accumulates across unrelated calls to the same shared dispatcher (used by multiple apps: IntentGatewayV2, HFT, WrappedHFT) via `ICallDispatcher(_dispatcher).dispatch(message.data)` [4](#0-3) ,

remains permanently on `CallDispatcher`'s own balance after the triggering transaction completes.

Because `CallDispatcher.dispatch(bytes memory encoded)` performs no caller authentication [5](#0-4) , any unprivileged address can, in a separate later transaction, call `dispatch()` directly on the shared `CallDispatcher` address with an arbitrary `Call[]` (e.g., an ERC20 `transfer` to itself) to move out any residual token or native balance sitting on that contract — exactly the same "unguarded execute() drains stranded assets" primitive as the reported `ProxyCall.execute()` issue.

## Impact Explanation
This is a direct fund-theft path: any residual stablecoin/ERC20/native balance left on the shared `CallDispatcher` — whether from partial-fill swap dust, multi-hop intermediate tokens not included in the declared input/output token list, or dust from any of the multiple apps (`IntentGatewayV2`, `HyperFungibleTokenUpgradeable`, `WrappedHyperFungibleToken`) that share this single dispatcher instance — is fully exposed to theft by an unprivileged, unrelated third party, since `dispatch()` has no caller restriction. This matches the bounty's "stealing or loss of funds" and "unauthorized execution" impact categories.

## Likelihood Explanation
Likelihood is high given normal usage: the sweep logic in `IntentsBase._execute()` and `IntentGatewayV2.placeOrder()`/`fillOrder()` only ever accounts for tokens explicitly enumerated in `order.inputs`/`order.output.assets`, while solver/user-supplied calldata routed through the shared dispatcher can legitimately touch other tokens (multi-hop routing, partial DEX fills, approvals to routers that later refund elsewhere). No malicious peer, relayer, or admin is required — any EOA can call the publicly known `CallDispatcher` address directly at any time once a residual balance exists.

## Recommendation
- Add caller authentication to `CallDispatcher.dispatch()` (e.g., restrict to a registered set of authorized app contracts, or make `CallDispatcher` a per-caller/per-tx ephemeral proxy rather than a shared singleton).
- Alternatively/additionally, ensure a full balance sweep of *all* tokens actually touched during a dispatch (not just the pre-declared input/output token list) back to the initiating app at the end of every `_execute`/predispatch flow, so no dust can persist between transactions.
- Add a `sweep`/`rescue` function restricted to the owning gateway(s) so any already-accrued dust can be recovered safely instead of being permanently exposed.

## Proof of Concept
1. A solver fills an order via `IntentGatewayV2.fillOrder()` whose `order.output.call` swaps through `CallDispatcher` using a multi-hop path where an intermediate token (not part of `order.output.assets`) is briefly held by the router and ends up back on `CallDispatcher`'s balance (e.g., a router refund of an unused approval, or a fee-on-transfer/partial-fill token as in the original report's `swap2` PoC).
2. `IntentsBase._execute()` only builds sweep calls for `order.output.assets[0..outputsLen)`, so the intermediate token balance is never swept and remains on the `CallDispatcher` contract after the transaction [6](#0-5) .
3. Any attacker (no privileges required) then calls, in a separate transaction:
```solidity
Call[] memory drain = new Call[](1);
drain[0] = Call({
    to: address(residualToken),
    value: 0,
    data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, residualToken.balanceOf(address(callDispatcher)))
});
ICallDispatcher(address(callDispatcher)).dispatch(abi.encode(drain));
```
Because `CallDispatcher.dispatch()` [1](#0-0)  performs no `msg.sender` check, this call succeeds and transfers the stranded tokens to the attacker.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L227-256)
```text
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
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleTokenUpgradeable.sol (L320-333)
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
```
