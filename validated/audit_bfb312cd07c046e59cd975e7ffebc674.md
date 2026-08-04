### Title
Unauthenticated `CallDispatcher.dispatch()` allows anyone to drain native ETH/ERC20 balances held by the shared dispatcher contract - (File: evm/src/utils/CallDispatcher.sol)

### Summary
The Axelar report flags that `executeCallViaAxelar`/`executeCallWithTokenViaAxelar` forward calls to an unverified, unhardened `destinationAddress`, so funds sent there can be lost if the target isn't the correct executor. The Hyperbridge local analog is stronger: `CallDispatcher.dispatch()` — the shared execution primitive used by `IntentGatewayV2`, `IntentsBase`, `HyperFungibleToken`, and `WrappedHyperFungibleToken` to move escrowed/unlocked funds — has **no access control at all** and blindly `call`s an attacker-chosen `to` with attacker-chosen `value`/`data`. Because this contract is meant to *hold* native ETH and ERC20 balances transiently (its `receive()` is payable, and several apps explicitly route unlocked/unwrapped funds "to the `CallDispatcher`" before further calls execute), any balance it is holding at any point in time can be swept by an unrelated, unprivileged caller with a single direct transaction.

### Finding Description
`CallDispatcher.dispatch()` is a fully public external function: [1](#0-0) 

It decodes an attacker-supplied `Call[]` and executes `to.call{value: call.value}(call.data)` for every entry, gated only by an `extcodesize(to) > 0` check — there is no `msg.sender` restriction, no `onlyHost`, no owner/whitelist check, and no binding to which application or cross-chain message authorized the call.

`CallDispatcher` is documented and used as a *shared, persistent* contract across multiple production apps that route real value through it:
- `HyperFungibleToken.onAccept` / `WrappedHyperFungibleToken.onAccept` invoke `ICallDispatcher(_dispatcher).dispatch(message.data)` after minting/unlocking tokens, and the docs explicitly describe setting the mint/unlock recipient `to` to the `CallDispatcher` address itself so it can hold native ETH or tokens before forwarding them via `Call.value`/ERC20 transfer: [2](#0-1) 
- `IntentGatewayV2.placeOrder` transfers order assets (native or ERC20) into the same `dispatcher` contract, then calls `dispatch()` on it, then sweeps the residual balance back: [3](#0-2) 
- `IntentsBase._execute` does the analogous thing on the fulfillment/output side, explicitly noting "any residual token balances left on the dispatcher are swept back... and accounted for as protocol dust": [4](#0-3) 

Because `CallDispatcher` is a single shared deployment referenced by address across all these apps (see `ConfigOptions.dispatcher` / `_params.dispatcher`), and because its `dispatch()` entrypoint is open to any caller, any native ETH or ERC20 balance that is resting in the contract — whether from dust left by an incomplete sweep, ETH deposited via its payable `receive()`, or funds mid-flight between the "transfer-in" and "sweep-out" steps of any of the above flows — is directly stealable. An attacker simply calls:
```solidity
Call[] memory calls = new Call[](1);
calls[0] = Call({to: attacker, value: address(callDispatcher).balance, data: ""});
callDispatcher.dispatch(abi.encode(calls));
```
or, for ERC20 dust:
```solidity
calls[0] = Call({to: token, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, token.balanceOf(address(callDispatcher)))});
```
This requires no malicious relayer, prover, or peer — it is a direct, permissionless call to a production contract.

### Impact Explanation
Any value the `CallDispatcher` is holding (native ETH from its payable `receive()`, or ERC20 dust from incomplete/partial sweeps in `IntentGatewayV2`/`IntentsBase`/`HyperFungibleToken` flows) can be stolen outright by an unprivileged third party. Since the same `CallDispatcher` deployment is reused across every app that references it (per the mainnet/testnet contract-addresses docs), any dust or transient balance from *any* user's cross-chain transfer or intent fill becomes a shared, permanently-drainable pool for whoever calls `dispatch()` first. This is a direct "loss of funds" / "unauthorized execution" impact matching the bounty's accepted classes.

### Likelihood Explanation
High for any window in which the contract holds a nonzero balance: it needs only one unauthenticated transaction, no cross-chain proof, no privileged role, no relayer collusion — just observing (or causing, via normal usage) a nonzero balance in the shared `CallDispatcher` contract. Multiple documented usage patterns (routing unlocked ETH "to" the dispatcher, sweeping "dust" after partial fills) guarantee that nonzero transient/residual balances are a normal, expected occurrence, not an edge case.

### Recommendation
Restrict `CallDispatcher.dispatch()` to a whitelisted set of authorized callers (e.g., only the specific `IHost`, `IntentGatewayV2`, `HyperFungibleToken` instances that are expected to invoke it), or make the dispatcher non-shared/ephemeral per-call (e.g., deploy per-flow via CREATE2/clone-and-selfdestruct-pattern) so it never persists a balance across transactions from unrelated callers. At minimum, add a check that any residual balance is swept back to the calling app atomically and that `dispatch()` cannot be invoked except from within the same transaction that funded it.

### Proof of Concept
1. Wait for (or trigger, e.g. via a `WrappedHyperFungibleToken` bridge-and-swap flow per the documented pattern) a state where `CallDispatcher` holds a nonzero native or ERC20 balance (dust from partial sweep, or ETH sent to its `receive()`).
2. From any EOA, call:
```solidity
Call[] memory calls = new Call[](1);
calls[0] = Call({to: attackerAddress, value: address(callDispatcher).balance, data: ""});
ICallDispatcher(callDispatcher).dispatch(abi.encode(calls));
```
3. The call succeeds (no access-control check exists) and transfers the entire resting balance to `attackerAddress`. [5](#0-4)

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

**File:** docs/content/developers/evm/hyper-fungible-token/wrapped-hyper-fungible-token.mdx (L176-201)
```text
// The CallDispatcher holds the unwrapped ETH and forwards it via Call.value
calls[0] = Call({
    to: UNISWAP_V2_ROUTER,
    // forward the native ETH to the router
    value: amount,
    data: abi.encodeWithSelector(
        IUniswapV2Router02.swapETHForExactTokens.selector,
        usdcAmountOut,
        path,
        recipientAddress,
        block.timestamp
    )
});

IHyperFungibleToken(address(wrapper)).send{value: nativeFee}(
    IHyperFungibleToken.SendParams({
        dest: StateMachine.evm(1),
        // unlock to the CallDispatcher so it receives the unwrapped ETH
        to: abi.encodePacked(CALL_DISPATCHER),
        amount: amount,
        timeout: 3600,
        relayerFee: relayerFee,
        data: abi.encode(calls)
    })
);
```
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L383-443)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            // Transfer all predispatch assets to the call dispatcher
            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;

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

            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L427-443)
```text
    /**
     * @dev Executes arbitrary calldata attached to an order's output via the CallDispatcher.
     * After dispatching the calls, any residual token balances left on the dispatcher
     * are swept back to this contract and accounted for as protocol dust.
     *
     * This enables composable order fulfillment — solvers can route through DEXes,
     * lending protocols, or other DeFi primitives as part of filling an order.
     *
     * @param order The order containing the output calldata to execute.
     * @param outputsLen The number of output assets to sweep after execution.
     */
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

```
