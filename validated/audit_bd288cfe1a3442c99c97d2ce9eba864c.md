### Title
`CallDispatcher.dispatch()` has no caller restriction and no target/selector allow-list, letting anyone drain any balance it holds - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
The bug report flags `LSSVMPair.call()` as risky because it lets an owner-authorized caller execute arbitrary target/calldata, and recommends restricting it (target allow-list + selector filtering) to stop misuse. Hyperbridge's `CallDispatcher` contract implements the same "arbitrary call" pattern used across `IntentGatewayV2`/`IntentsBase` (predispatch/postdispatch) and the HyperFungibleToken calldata-execution feature, but goes further than the reported issue: `dispatch()` has **no `onlyOwner`/`onlyGateway` check at all**, and no target or selector filtering — any address can call it directly.

### Finding Description
`CallDispatcher.dispatch()` is `external` with no access-control modifier: [1](#0-0) 

It decodes an attacker-supplied `Call[]` and executes `to.call{value: call.value}(call.data)` for each entry, only checking that `to` has code (`extcodesize > 0`) — there is no restriction on which contract may call `dispatch()`, and no whitelist/blacklist of destination selectors (unlike the `LSSVMPair.call()` case where at least `factory().callAllowed(target)` gates the target).

The contract is also payable and shared as a single, protocol-wide singleton instance (one `dispatcher` address configured in `Params` and reused by every order's predispatch/postdispatch calls): [2](#0-1) 

`IntentsBase._execute()` transfers order output assets to the dispatcher, calls `dispatch()` with the order's calldata, and then sweeps back only the tokens listed in `order.output.assets` (`outputsLen`): [3](#0-2) 

Because the sweep loop only iterates over `outputsLen` (the assets declared in the order), any token or ETH that ends up in the `CallDispatcher` that is **not** part of `order.output.assets` — e.g., a reward/output token produced by the postdispatch DeFi call that differs from the declared output token, dust from a failed/partial sweep, or ETH sent to the contract's public `receive()` — is never collected by the legitimate flow and remains sitting in the shared `CallDispatcher` balance. Since `dispatch()` has no caller restriction, any external, unprivileged address can then call `CallDispatcher.dispatch()` directly with a `Call[]` that transfers that stranded balance to itself.

### Impact Explanation
This is a public entrypoint that lets an unprivileged attacker cause unauthorized execution and fund loss: any ETH or ERC-20 balance the shared `CallDispatcher` holds outside of an atomic order-fill transaction (uncollected dust, mis-swept tokens from postdispatch DeFi routing, or accidental/forced ETH transfers via `receive()`) can be swept to an arbitrary address by anyone, not just the `IntentGatewayV2`/HFT contracts that are supposed to be the only legitimate callers. This matches the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" categories, and requires no malicious peer, relayer, prover, or admin — only a public call to an unauthenticated, unrestricted `external` function.

### Likelihood Explanation
Medium-to-high: exploitation does not require compromising any privileged role — it only requires the shared `CallDispatcher` to (even transiently) hold funds that the `outputsLen`-bound sweep in `IntentsBase._execute()` does not account for (e.g., postdispatch calldata that yields a token not present in `order.output.assets`, or partial-success DeFi interactions), or ETH sent to its `receive()`. Given `CallDispatcher` is a single, long-lived, protocol-wide contract reused by every order (predispatch/postdispatch) and by `HyperFungibleToken`/`WrappedHyperFungibleToken` calldata execution, the surface for accumulating unswept balances is broad, and the drain itself is a single unauthenticated call.

### Recommendation
Restrict `CallDispatcher.dispatch()` to only be callable by the authorized `IntentGatewayV2`/token contracts that are configured as its owners (analogous to `factory().callAllowed(target)` in the reported `LSSVMPair.call()` fix), e.g., an `onlyAuthorizedCaller` modifier checked against a registry of gateway/token addresses. Additionally, ensure `IntentsBase._execute()`'s sweep step accounts for *all* tokens the dispatcher could plausibly receive during a call (not just `order.output.assets`), or have the dispatcher self-destruct/reset any transient allowance after each dispatch so no balance can persist between transactions for an outside caller to claim.

### Proof of Concept
1. Deploy/observe the shared `CallDispatcher` instance referenced by `IntentGatewayV2`'s `Params.dispatcher` [4](#0-3) .
2. Have a solver fill an order whose `PaymentInfo.call` (postdispatch calldata) routes output tokens through a DeFi call that yields a token not listed in `order.output.assets` (or leaves rounding dust of the listed token) — per `IntentsBase._execute()`, only `outputsLen` tokens are swept back, so any other token/ETH remains in the dispatcher [5](#0-4) .
3. From any unprivileged EOA, call `CallDispatcher.dispatch(abi.encode(calls))` directly, with `calls` transferring the stranded token/ETH balance to the attacker's address — this succeeds because `dispatch()` performs no caller check [6](#0-5) .
4. The attacker walks away with funds that were never intended for them, with no interaction from the `IntentGatewayV2`, no relayer/prover compromise, and no admin action involved.

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-474)
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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L143-153)
```text
        intentGateway.initialize(
            Params({
                host:            address(host),
                dispatcher:      address(dispatcher),
                solverSelection: false,
                surplusShareBps: 0,
                protocolFeeBps:  0,
                priceOracle:     address(0)
            }),
            new bytes[](0)
        );
```
