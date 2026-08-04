I found a solid, locally-provable analog. It's not a facet like `GenericBridgeFacet`, but the pattern is functionally identical: an unrestricted arbitrary-call primitive that anyone can invoke against a shared contract holding value.## Finding: Permissionless Arbitrary-Call `CallDispatcher.dispatch()` Allows Draining Any Value Held By the Shared Dispatcher

This is a direct local analog to the `GenericBridgeFacet` bug: an unrestricted, un-whitelisted `to.call{value}(data)` primitive exposed on a `public`/`external` function that anyone can invoke, on a contract that legitimately (and, per its own docs, temporarily) holds tokens and ETH belonging to third parties.

### Summary

`CallDispatcher.dispatch()` is a fully public function with **no caller restriction whatsoever** — unlike virtually every other privileged entry point in the codebase (e.g. `HostManager`, `EvmHost`), which use a `restrict(...)` modifier to gate cross-chain/administrative calls. `CallDispatcher` is a **shared singleton** used by `IntentGatewayV2`/`IntentsBase`, `HyperFungibleToken`, and `WrappedHyperFungibleToken` across all users and all chains, and it is designed to hold ETH/ERC20 balances mid-flow. Because `dispatch()` has no access control, any address currently holding a balance in the dispatcher (dust from swaps, stray direct transfers via its public `receive()`, or any balance not yet swept back) can be stolen by an arbitrary unrelated caller. [1](#0-0) 

### Finding Description

`CallDispatcher.dispatch()`:
```solidity
function dispatch(bytes memory encoded) external {
    Call[] memory calls = abi.decode(encoded, (Call[]));
    ...
    (bool success, bytes memory result) = to.call{value: call.value}(call.data);
    if (!success) revert CallFailed(to, result);
}
```
takes attacker-controlled `to`, `value`, and `data`, and executes the call using the **dispatcher's own balance** for `value` — there is no `msg.sender` check, no whitelist of callers, and no whitelist of targets/selectors. [2](#0-1) 

`CallDispatcher` also accepts arbitrary ETH via a public `receive()`: [3](#0-2) 

It is a **shared, address-stable singleton** (deployed once per chain via CREATE2) used by multiple independent apps: `IntentGatewayV2`/`IntentsBase` (predispatch/output execution + sweep), `HyperFungibleToken`, and `WrappedHyperFungibleToken` (post-mint/unlock calldata execution) — all of these route tokens *through* the dispatcher before sweeping the balance back. [4](#0-3) [5](#0-4) 

The protocol's own documentation acknowledges dust can be left behind in the dispatcher (`DustCollected` events, "protocol dust" comments), and separately warns integrators to use exact-amount approvals "since the dispatcher contract holds tokens temporarily during execution" — i.e. the design assumes atomic same-transaction sweep-back is the only protection, but that guarantee does not hold once any balance is left resident in the dispatcher (from a revert leaving prior state, an unaccounted output token not included in `outputsLen`, rounding dust, or a stray direct ERC20/ETH transfer to the dispatcher's address by any third party). [6](#0-5) 

Because `dispatch()` is callable by anyone, any balance sitting in `CallDispatcher` at any point in time — between transactions, or from any integrator that doesn't perfectly sweep 100% of output/predispatch assets — is a bounty for any unrelated address, exactly mirroring the `GenericBridgeFacet`/`LibSwap.swap()` primitive that let anyone drive an arbitrary `.call()` using assets the calling contract had claim to.

### Impact Explanation

Loss of funds: any ETH or ERC20 balance resident in the shared `CallDispatcher` — dust from intent fills, unswept partial amounts, or accidental/stray transfers — can be stolen by any unprivileged address by simply calling `dispatch()` with a `Call` targeting the token contract (`transfer(attacker, balance)`) or forwarding the dispatcher's ETH balance directly to the attacker. This requires no malicious relayer, prover, governance actor, or leaked key — it is a pure permissionless-invocation bug matching the Impact Gate's "stealing or loss of funds" / "unauthorized transaction or execution" criteria.

### Likelihood Explanation

High: `dispatch()` has zero guards, is `external`, and the dispatcher is a known, deployed, address-stable singleton actively used by production flows (`IntentGatewayV2`, HFT/WrappedHFT) that intentionally route value through it. The attack requires only calling a public function with a crafted `Call[]` payload — no privileged position, no race condition beyond simply observing an on-chain balance in the dispatcher.

### Recommendation

Restrict `CallDispatcher.dispatch()` to authorized callers only (e.g. a `restrict`-style allowlist of registered app contracts, mirroring the pattern already used by `HostManager`/`EvmHost`), or make the dispatcher ephemeral/per-call (deployed fresh per invocation, e.g. via minimal proxy/clone-and-selfdestruct pattern) so it never carries residual balance across calls or transactions. At minimum, sweep 100% of any token that could conceivably remain in the dispatcher (not just the declared `outputsLen` output assets) at the end of every flow that uses it, and disallow the bare `receive()` from accepting unsolicited transfers.

### Proof of Concept

1. Any user places/fills an intent order (or bridges via HFT) whose `output.call`/`data` performs a swap or multi-step call that, due to slippage, partial fill, or an extra token not listed in `order.output.assets`, leaves an ERC20 balance or ETH balance resident in the shared `CallDispatcher` after the legitimate flow's sweep completes (or reverts leaving state uncleared, e.g. `receive()` is hit directly by an unrelated deposit).
2. An unrelated attacker observes the dispatcher's on-chain token/ETH balance.
3. Attacker calls `CallDispatcher.dispatch(abi.encode(calls))` directly with:
   ```solidity
   Call[] memory calls = new Call[](1);
   calls[0] = Call({
       to: leftoverToken,
       value: 0,
       data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, leftoverToken.balanceOf(dispatcherAddr))
   });
   ```
   or, for native ETH:
   ```solidity
   calls[0] = Call({to: attacker, value: address(dispatcherAddr).balance, data: ""});
   ```
4. `dispatch()` executes the call with no `msg.sender` check, transferring the dispatcher's held balance to the attacker.

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-468)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L203-227)
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
```
