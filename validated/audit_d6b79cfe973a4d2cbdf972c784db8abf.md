## Analysis

The external report's core broken invariant: **an external/message call executed "as" a value-holding contract, with no restriction on who can trigger it, exposes any balance that contract holds to arbitrary confiscation.** In RubiconMarket, `this.offer` accidentally made the market itself the offer-maker; anyone could then take the offer and drain the market's escrow.

The direct local analog is `CallDispatcher.dispatch()`.

### The vulnerable pattern

`CallDispatcher.dispatch()` is `external` with **no caller restriction whatsoever** — no `onlyOwner`, no `onlyGateway`, nothing: [1](#0-0) 

Yet `CallDispatcher` is the *shared, persistent, singleton* contract that `IntentGatewayV2` (and `HyperFungibleToken`/`WrappedHyperFungibleToken`) actually transfers real user funds into before executing arbitrary calldata on their behalf, during `placeOrder`'s predispatch flow: [2](#0-1) 

and during order fulfillment's postdispatch flow (`_execute`): [3](#0-2) 

After the calldata executes, only the tokens explicitly enumerated in `order.inputs` (predispatch) or `order.output.assets` (postdispatch) are swept back out of the dispatcher: [4](#0-3) [5](#0-4) 

Any byproduct balance the predispatch/postdispatch calldata produces that is **not** one of those enumerated tokens (e.g. an LP-unwrap or swap that yields an extra token, or leftover native ETH from a partially-consumed native transfer to the dispatcher) is never swept and is left sitting in `CallDispatcher`'s own balance indefinitely. `CallDispatcher` also accepts and holds native ETH via its unrestricted `receive()`: [6](#0-5) 

Because `dispatch()` has no access control, **anyone** — not the order owner, not the gateway, not a privileged relayer — can call `CallDispatcher.dispatch()` directly with a `Call[]` that targets the stranded token/ETH and moves it to themselves. The only gate in `dispatch()` is that `call.to` must have code (`NotContract`); there is no check that the caller is the `IntentGatewayV2` or any other legitimate integrator, and no check on what `call.data`/`call.to`/`call.value` may be.

This is structurally identical to the Rubicon bug: a contract that legitimately custodies value performs an externally-triggerable action ("make an offer" / "dispatch an arbitrary call") that is not gated to the intended caller, letting an unprivileged attacker redirect the custodied value to themselves.

### Title
Unauthenticated `CallDispatcher.dispatch()` lets anyone drain any token/ETH balance stranded in the shared dispatcher - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch()` has no caller restriction. `IntentGatewayV2` (and the HyperFungibleToken apps) route real user funds through this shared, persistent contract during predispatch/postdispatch calldata execution, but only sweep back the specific tokens enumerated in the order's `inputs`/`output.assets`. Any other token or native ETH produced or left over by predispatch/postdispatch calldata remains parked in `CallDispatcher`'s balance and can be swept out by any unprivileged caller who directly invokes `dispatch()` with their own `Call[]`.

### Finding Description
`dispatch(bytes memory encoded)` decodes an attacker-supplied `Call[]` and executes `to.call{value: call.value}(call.data)` for each entry, with `CallDispatcher` itself as `msg.sender`/token holder [7](#0-6) . It performs no `onlyOwner`/`onlyGateway` check and is externally callable by any address.

`IntentGatewayV2.placeOrder` and `IntentsBase._execute` intentionally push real ERC20/native balances into this same dispatcher instance and then invoke user- or solver-supplied calldata (`order.predispatch.call`, `order.output.call`) through it [2](#0-1) [3](#0-2) . The subsequent sweep only recovers the specific `order.inputs[i].token` / `order.output.assets[i].token` amounts [4](#0-3) [5](#0-4) . Any token or native ETH not in those explicit lists — e.g. a byproduct of a swap/unwrap executed in the predispatch/postdispatch calldata, or ETH sent to fund a predispatch asset that the calldata does not fully consume — is left in `CallDispatcher`'s balance with no code path to reclaim it except through `dispatch()` itself, which anyone can call.

### Impact Explanation
Direct, unauthorized theft of funds that legitimately belong to the protocol/users but happen to be resting in the shared `CallDispatcher`. Since `dispatch()` allows arbitrary `to`/`data`/`value`, an attacker can move the entire stranded balance (ERC20 via `transfer`, or native ETH via `call.value`) to any address they control, in a single unprivileged transaction — no relayer, prover, admin, or governance action is required.

### Likelihood Explanation
Any predispatch/postdispatch order whose attached calldata produces a token not listed in `order.inputs`/`order.output.assets`, or that under- or over-consumes a native-ETH prefund, creates exploitable dust in `CallDispatcher`. Given the gateway's documented composability (arbitrary DeFi calldata, swaps, LP unwraps) [8](#0-7) , such mismatches are a normal and expected occurrence, not an edge case, and the drain requires nothing beyond calling a public function.

### Recommendation
Restrict `CallDispatcher.dispatch()` to a caller allowlist (e.g. `onlyOwner`/`onlyGateway`, set at construction) so only the integrating app contracts (`IntentGatewayV2`, `HyperFungibleToken`, etc.) can trigger it. Additionally, ensure any calldata-produced token/ETH balances are fully swept back to the invoking app (sweep by querying actual post-call balances for *all* tokens touched, not just the ones declared in `order.inputs`/`order.output.assets`), so `CallDispatcher` never carries a standing balance between transactions.

### Proof of Concept
1. A user places an order with `predispatch.call` that swaps/unwraps into two tokens, A and B, but `order.inputs` only lists token A (token B is an incidental byproduct, e.g. leftover reward token from an LP unwrap).
2. `placeOrder` executes the predispatch call through `CallDispatcher`, then sweeps only token A back to the gateway; token B remains in `CallDispatcher`'s balance [9](#0-8) .
3. An attacker, in a separate transaction, calls `CallDispatcher.dispatch(abi.encode([Call({to: tokenB, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, tokenB.balanceOf(dispatcher))})]))`.
4. `dispatch()` executes this call with no authorization check, transferring all of token B held by `CallDispatcher` to the attacker.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L36-61)
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-443)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L444-474)
```text
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

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L83-103)
```text
## Calldata

Orders support arbitrary calldata execution at two points in the lifecycle — before escrow (predispatch) and after fill (postdispatch). Both are executed through the `CallDispatcher` contract, which takes an ABI-encoded `Call[]` array:

```solidity
struct Call {
    address to;      // Target contract (must have code, reverts with NotContract otherwise)
    uint256 value;   // ETH to send with call
    bytes data;      // Calldata to execute
}
```

The `CallDispatcher` executes each call sequentially and reverts the entire batch if any call fails.

### Predispatch

The `predispatch` field in `Order` contains calldata to execute *before* escrowing inputs. The predispatch assets specified in `DispatchInfo.assets` are transferred to the `CallDispatcher`, the encoded calls are executed, and the resulting tokens are transferred back to the gateway for escrow. This enables swap-then-escrow patterns — for example, a user sends ETH which the `CallDispatcher` swaps to DAI on Uniswap, and the resulting DAI is escrowed as the order input.

### Postdispatch

The `call` field in `PaymentInfo` contains calldata to execute *after* the order is filled. This enables fill-then-act patterns — for example, output tokens received from the solver are routed through a DeFi protocol before reaching the beneficiary.
```
