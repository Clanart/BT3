Confirmed: `CallDispatcher.dispatch(bytes)` at `evm/src/utils/CallDispatcher.sol:44` has **no access control whatsoever** — it is `external` with no `restrict`/`onlyOwner`/caller check of any kind. It executes an arbitrary attacker-supplied `Call[]` from the CallDispatcher's own address, and it has a `receive()` function so it accumulates ETH. [1](#0-0) 

This is the real local analog of the Morpho isolation-mode bug: a **single shared contract instance** (the `CallDispatcher`, deployed once via `CREATE2` and reused across every `placeOrder`/`fillOrder` predispatch/postdispatch step in `IntentGatewayV2`) is treated by the surrounding protocol as if its balance always corresponds cleanly to "whatever the current order sent it." But because `dispatch` is public and unauthenticated, and the contract has a `receive()`, anyone can (a) donate/park tokens or ETH on it directly, or (b) simply call `dispatch()` themselves with a `Call[]` that transfers out whatever balance currently sits on it — exactly like Morpho's single Aave position picking up whatever asset happened to land on it first, and then having subsequent code make incorrect assumptions about that shared state.

### Title
Unauthenticated `CallDispatcher.dispatch()` lets anyone drain shared escrow-in-transit balances - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`CallDispatcher` is a single, address-deterministic (`CREATE2`) contract shared by `IntentGatewayV2` (and other `HyperApp`s) to execute predispatch/postdispatch calldata. `IntentGatewayV2.placeOrder`/`fillOrder` route user tokens through it as a transient holding contract during swap-then-escrow flows, then sweep `balanceOf(dispatcher)` back to the gateway [2](#0-1) . Because `CallDispatcher.dispatch()` has no caller restriction, any external account can invoke it directly with an arbitrary `Call[]`, executing calls "as" the dispatcher.

### Finding Description
`dispatch()` only checks that `call.to` has code; it never checks `msg.sender` [3](#0-2) . The protocol's escrow-accounting design assumes the dispatcher's balance during a given `placeOrder`/`fillOrder` call reflects only the assets that call itself routed there — the gateway reads `IERC20(token).balanceOf(dispatcher)` right after its own `dispatch(order.predispatch.call)` invocation and treats the *entire* balance as belonging to the order in progress: it transfers all of it back and computes `dust = balance - requiredAmount` [4](#0-3) . This mirrors Morpho's flaw: the shared aggregate object (Aave position / here, the shared CallDispatcher) has state that isn't scoped to a single logical actor, and code downstream (collateral logic / escrow-sweep logic) implicitly trusts that state to be "clean" or attributable to the current caller.

Because `dispatch` is callable by anyone, an attacker can, between two legitimate `placeOrder`/`fillOrder` invocations that both happen to route through the same predispatch/postdispatch calldata pattern in the same block (e.g. via front-running a pending transaction, or racing a solver's `fillOrder` postdispatch execution), call `dispatch()` directly on the dispatcher to pull out whatever tokens are sitting there mid-flow (e.g. tokens sent to the dispatcher by `placeOrder`'s predispatch asset transfer but not yet swept back by the second `dispatch` call in the same function) before the legitimate sweep executes. The `IntentGatewayV2` code performs multiple separate external calls to the dispatcher within one logical operation (transfer assets → `dispatch(predispatch.call)` → `dispatch(transferCalls)`), and none of those intermediate states are protected — anyone racing a transaction targeting the dispatcher address can insert their own `dispatch()` call to sweep the dispatcher's current balance to themselves before the gateway's own sweep call executes.

### Impact Explanation
An attacker can steal user funds mid-flight from the shared `CallDispatcher`: assets a user has already transferred for escrow (native ETH or ERC-20) but which have not yet been swept back into `IntentGatewayV2`'s own balance are, for a duration spanning multiple transactions/calls, freely withdrawable by any address via `dispatch()`. This is unauthorized transaction/execution and direct loss of user-escrowed funds, matching the bounty's "stealing or loss of funds" and "unauthorized execution" categories.

### Likelihood Explanation
Exploitability requires only observing pending transactions that route funds to the well-known, deterministic `CallDispatcher` address and racing a `dispatch()` call against them — no privileged role, relayer, prover, or governance compromise is needed, satisfying the "unprivileged public entrypoint" requirement. The severity is bounded by how much value transiently sits on the dispatcher and by mempool visibility, but the entrypoint itself is completely open.

### Recommendation
Restrict `CallDispatcher.dispatch()` to a registered caller (e.g., only the `IntentGatewayV2`/`HyperApp` instance(s) that deployed or are authorized to use it), or make the dispatcher single-use/ephemeral per order (e.g., deploy a minimal-proxy dispatcher per order via `CREATE2` with the order commitment as salt) so no shared, publicly-drainable balance ever exists. At minimum, add a `restrict(authorizedCaller)` modifier mirroring the pattern already used in `HostManager.sol`'s `restrict` modifier [5](#0-4) .

### Proof of Concept
1. Monitor the mempool for a `placeOrder` call to `IntentGatewayV2` that uses `predispatch.assets` with `predispatch.call.length > 0` (routes tokens through the shared `CallDispatcher`).
2. Once the victim's transaction transfers `predispatch.assets` (native ETH via `dispatcher.call{value: amount}("")` or ERC-20 via `safeTransferFrom(msg.sender, dispatcher, amount)`) but before its `ICallDispatcher(dispatcher).dispatch(transferCalls)` sweep completes [6](#0-5) , submit a higher-gas transaction calling `CallDispatcher.dispatch()` directly with a `Call[]` that transfers the dispatcher's current token/ETH balance to the attacker's address.
3. Since `dispatch()` performs no authorization check, the call succeeds, and the attacker receives the victim's in-transit escrow assets before the gateway's own sweep executes (which will then revert or process with insufficient balance).

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L386-435)
```text
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
```

**File:** evm/src/core/HostManager.sol (L56-60)
```text
    // @dev restricts call to the provided `caller`
    modifier restrict(address caller) {
        if (msg.sender != caller) revert UnauthorizedAction();
        _;
    }
```
