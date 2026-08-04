## Analysis

The seed report's core broken invariant: an internal helper grants an ERC-20 allowance sized to a *maximum* possible pull rather than an on-demand exact amount, and never resets it after use — leaving a standing allowance on a contract that holds real user funds, exploitable if the counterparty (or its calldata) is later abused.

Hyperbridge's `CallDispatcher` reproduces this pattern but in a worse form: the contract that receives the standing approvals is **shared across every order/message that uses calldata execution**, and its `dispatch()` entrypoint has **no caller restriction at all**.### Title
Permissionless `CallDispatcher.dispatch()` lets attacker-planted ERC-20 approvals drain dust left by other users' orders - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher` is a single, shared contract used by `IntentGatewayV2` (predispatch/postdispatch calldata) and by `HyperFungibleToken`/`WrappedHyperFungibleTokenUpgradeable` (`onAccept` calldata) across *all* users and *all* orders. Its `dispatch()` function is `external` with **no caller restriction whatsoever** — anyone can invoke it directly, not only the app contracts that are supposed to own it.

### Finding Description
`dispatch()` blindly executes attacker-supplied `Call[]` on behalf of the shared `CallDispatcher`: [1](#0-0) 

Because any caller can invoke `dispatch()`, and any order's `predispatch.call` / `output.call` is fully attacker-controlled at order-placement time, an attacker can place a trivial order (e.g. 1 wei of `USDC`) whose calldata is:

```
Call{ to: USDC, data: approve(attacker, type(uint256).max) }
```

`IntentGatewayV2.placeOrder()` funnels this through the dispatcher exactly like the seed report's `_mintFCashPosition`: it grants an allowance sized for a hypothetical maximum, then sweeps only the tokens explicitly listed in `order.inputs`: [2](#0-1) 

The sweep only ever iterates over `order.inputs`/`order.output.assets` — any other token balance the dispatcher accumulates (partial swap residue, a token not enumerated in `order.inputs`, fee-on-transfer remainders, or tokens sent by unrelated `HyperFungibleToken` messages that route `to: CALL_DISPATCHER`) is never cleared and is not scoped to any particular order or user: [3](#0-2) 

Because `dispatch()` has no access control and grants approvals with no automatic revocation (identical to the seed report's missing "approve on-demand, reset to zero" mitigation), a standing approval planted once by an attacker remains valid indefinitely. Any subsequent dust of that same token that lands in the shared dispatcher — from any other user's order, from `HyperFungibleToken.send()` minting `to: CALL_DISPATCHER` and awaiting later spend-calls, or from partially-consumed swap calldata — is directly withdrawable by the attacker via a plain `TOKEN.transferFrom(dispatcher, attacker, balance)`, entirely outside of any Hyperbridge contract.

### Impact Explanation
The dispatcher is not order-scoped or user-scoped custody: its balance is a shared pool. A standing, unbounded ERC-20 allowance combined with an unauthenticated `dispatch()` entrypoint means any token dust the dispatcher accumulates — whether from bugs, fee-on-transfer mismatches, or attacker-engineered predispatch calldata that intentionally leaves non-input-token residue behind — is a permanent bounty for whoever planted an approval first. This is unauthorized transfer of bridge-adjacent funds to an unintended beneficiary, matching the bounty's "stealing or loss of funds" / "unauthorized transaction" categories.

### Likelihood Explanation
Planting the approval costs only a trivial order (dust amount) and normal gas; no relayer, prover, governance, or malicious peer is required — it's a fully self-serve, unprivileged-attacker action. The remaining question (how much/how often dust accumulates) depends on real-world calldata usage patterns; the underlying design flaw — a shared, permissionlessly-callable dispatcher whose approvals are never capped or revoked — is verifiable directly from the code regardless of how frequently dust actually appears.

### Recommendation
- Restrict `CallDispatcher.dispatch()` to an allowlist of authorized callers (the `IntentGatewayV2` and HFT contracts that are supposed to own it), or deploy a fresh, single-use dispatcher per order/message instead of one long-lived shared instance.
- Never allow arbitrary `approve()` targets/amounts to persist: after executing attached calldata, explicitly revoke (`approve(target, 0)`) any allowances the calldata created, or force all token movement through `transferFrom`-free custody patterns.
- Sweep *all* token balances the dispatcher holds after each `dispatch()` invocation, not only the tokens enumerated in `order.inputs`/`order.output.assets`, so no residual balance of any token can accumulate.

### Proof of Concept
1. Attacker calls `IntentGatewayV2.placeOrder()` with a dust order (`inputs = [1 wei USDC]`) whose `predispatch.call` encodes `Call{to: USDC, data: approve(attacker, type(uint256).max)}`. This executes via `ICallDispatcher(dispatcher).dispatch(order.predispatch.call)` [4](#0-3) , granting the dispatcher's `USDC` allowance to the attacker with no cap or expiry.
2. Over time, USDC dust accumulates in the same shared `CallDispatcher` from unrelated orders/messages (e.g., a predispatch swap whose calldata leaves rounding residue, or `HyperFungibleToken.send(to: CALL_DISPATCHER, data: calls)` where `calls` doesn't fully consume the minted balance).
3. Attacker calls `USDC.transferFrom(dispatcher, attacker, USDC.balanceOf(dispatcher))` directly — no interaction with any Hyperbridge contract is needed since the allowance from step 1 already authorizes it — draining the accumulated dust regardless of which user's order produced it.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L227-258)
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
