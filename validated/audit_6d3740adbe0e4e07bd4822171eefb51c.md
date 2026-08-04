### Title
Anyone can call the shared `CallDispatcher.dispatch()` to seize any balance transiently held by the dispatcher, draining dust/tokens meant for the protocol or in-flight order flows - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch(bytes)` is a fully public, unauthenticated entry point that executes an attacker-supplied `Call[]` array from the dispatcher's own address (not `delegatecall`). The same `CallDispatcher` instance is shared and reused by `IntentGatewayV2` (predispatch/postdispatch flows) and by `HyperFungibleToken`/`WrappedHyperFungibleToken` (bridge-and-call flows), all of which route funds *through* the dispatcher and rely on a follow-up "sweep" step to move funds back out. Because `dispatch()` has no caller restriction, any address holding at some point a non-zero balance in the dispatcher (dust left behind by an incomplete sweep, native ETH sent to its `receive()`, or tokens minted/transferred to it mid-flow) can be stolen by an unrelated third party who simply calls `dispatch()` first, before the legitimate owner's sweep executes. This mirrors the `Option.exercise()` bug: an unauthenticated caller can force execution against a contract ("receiver"/custodian) that itself performs no validation of who is entitled to trigger it, extracting value that belongs to someone else.

### Finding Description
`CallDispatcher.sol` is intentionally minimal: [1](#0-0) 

```solidity
function dispatch(bytes memory encoded) external {
    Call[] memory calls = abi.decode(encoded, (Call[]));
    ...
    (bool success, bytes memory result) = to.call{value: call.value}(call.data);
    if (!success) revert CallFailed(to, result);
}
```

There is no `onlyGateway`, `onlyHost`, or any `restrict` modifier — `dispatch()` can be called by literally any address, with an arbitrary `Call[]` targeting any address, forwarding any ETH balance the dispatcher currently holds and any calldata the caller wants.

The dispatcher is used as a shared, address-reused (CREATE2), balance-holding intermediary across multiple protocols:

- `IntentGatewayV2` transfers `order.predispatch.assets` to the dispatcher, runs the user-supplied `predispatch.call`, and only *afterward* issues a second `dispatch()` call to sweep the resulting balance back to the gateway: [2](#0-1) 
- `IntentsBase._execute` runs `order.output.call` through the same dispatcher and then sweeps any residual balance back as protocol "dust": [3](#0-2) 
- `HyperFungibleToken`/`WrappedHyperFungibleToken` mint or unlock bridged tokens directly to the `CallDispatcher` address so that subsequent calldata (e.g. Uniswap swaps) can spend them: [4](#0-3) 

All of these flows treat the dispatcher as a temporary custodian that "will be swept afterward." Because the sweep is a *separate*, later `dispatch()` invocation rather than an atomic, access-controlled step, and because `dispatch()` itself has no caller check, any balance sitting in the dispatcher at any point in time (leftover dust from a prior transaction's imperfect sweep, native ETH sent to `receive()`, or a token type not covered by a sweep's fixed asset list) is directly and permissionlessly drainable: an attacker can simply call `CallDispatcher.dispatch(abi.encode([Call({to: token, value: 0, data: transfer(attacker, balance)})]))` themselves.

The docs even acknowledge that dust can be left in the dispatcher ("any tokens remaining in the CallDispatcher are swept back to the gateway and collected as dust") — that protocol-owned dust, and any other balance the dispatcher legitimately custodies mid-flow, is exactly the kind of unprotected receiver balance the M03 report warns about: a caller with no relationship to the funds can trigger execution against the custodian and extract value intended for someone else.

### Impact Explanation
This maps to the bounty's fund-loss / wrong-beneficiary criteria: value that should flow to the protocol treasury (dust swept via `IntentsBase._execute`/`_withdraw`, emitting `DustCollected`) or should remain earmarked for an in-flight order/bridge operation can instead be redirected to an arbitrary attacker address, with no privileged role, relayer, or prover involved — a plain EOA calling a public Solidity function.

### Likelihood Explanation
High for the "steal residual dust/incidental balances" scenario: the dispatcher is a long-lived, address-stable, shared contract across many deployments and product flows (IntentGatewayV2, HyperFungibleToken, WrappedHyperFungibleToken, per-chain via CREATE2), so any balance that accrues in it between a transfer-in and its corresponding sweep-out — including balances left by reverted/partial batches, native ETH sent by mistake, or tokens outside the tracked asset list — is watchable on-chain and immediately stealable by anyone racing the legitimate sweep transaction.

### Recommendation
Restrict `CallDispatcher.dispatch()` to a caller allow-list (e.g., an `onlyOwner`/`onlyAuthorizedCaller` modifier configured per-deployment to the `IntentGatewayV2`/`HyperFungibleToken`/`WrappedHyperFungibleToken` instances that are meant to use it), or make each dispatcher instance single-tenant and non-reentrant so that only the contract that funded it can trigger execution. At minimum, add a `nonReentrant` guard and ensure every flow that routes funds through the dispatcher fully sweeps all balances (not just the tracked input/output token list) within the same atomic transaction before returning control to any caller.

### Proof of Concept
1. Deploy/observe the shared `CallDispatcher` instance used by `IntentGatewayV2` (address is public/documented per chain).
2. Monitor its ERC20/native balance (e.g., via `token.balanceOf(dispatcherAddress)` or its ETH balance) for any non-zero residual — this occurs naturally whenever a predispatch/postdispatch flow leaves dust, or when native ETH is sent to its `receive()`.
3. As soon as a non-zero balance is observed, submit a plain transaction:
```solidity
Call[] memory calls = new Call[](1);
calls[0] = Call({
    to: token,
    value: 0,
    data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, token.balanceOf(dispatcherAddress))
});
ICallDispatcher(dispatcherAddress).dispatch(abi.encode(calls));
```
4. Because `dispatch()` performs no caller check, this succeeds and transfers the balance to `attacker`, regardless of who deposited it or what protocol flow intended to sweep it later.

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

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L86-98)
```text
## Calldata Execution

Both contracts support optional calldata execution on the destination chain via the `CallDispatcher`. By passing a non-empty `data` field in `SendParams`, the sender can trigger arbitrary contract calls on the destination chain immediately after tokens are minted or unlocked. This enables composable cross-chain workflows like transfer-and-swap (e.g., bridge USDC then swap to WETH via UniswapV2), transfer-and-stake, or transfer-and-deposit into a lending protocol — all in a single cross-chain operation.

The `data` field is an ABI-encoded `Call[]` array, where each `Call` specifies a target contract, a native value to forward, and the calldata to execute. On the destination chain, the HFT contract mints or unlocks tokens to the `to` address, then forwards the entire `data` payload to the `CallDispatcher`, which executes each call sequentially. If the calls need to spend the bridged tokens (e.g., approve then swap), set `to` to the `CallDispatcher` address so tokens are delivered directly to it.

For code examples, see the [HyperFungibleToken](/developers/evm/hyper-fungible-token/hyper-fungible-token#calldata-execution) and [WrappedHyperFungibleToken](/developers/evm/hyper-fungible-token/wrapped-hyper-fungible-token#calldata-execution) pages.

### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```
