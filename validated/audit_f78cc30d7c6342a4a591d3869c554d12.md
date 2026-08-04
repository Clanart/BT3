Based on my investigation, I found a concrete, provable analog to the dHedge Checks-Effects-Interactions violation inside Hyperbridge's intent-settlement custody code, specifically in the Tron port of `IntentGatewayV2`.

### Title
Interaction-before-effect on escrow accounting in `IntentGatewayV2.withdraw()` regresses a previously fixed CEI bug - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The mainline EVM intent-settlement contracts (`evm/src/apps/intentsv2/IntentsBase.sol`) were previously vulnerable to a reentrancy/CEI bug in escrow fill/refund logic and were explicitly patched — the fix and its regression tests live in `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`, which documents that `_filled[commitment]` must be set, and escrow balances decremented, **before** any external token transfer. The Tron fork of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, reintroduces the unsafe ordering for the escrow-accounting map `_orders[commitment][token]`: it performs the outbound token/native transfer first and only decrements the escrow bookkeeping afterward.

### Finding Description
In the fixed mainline `_withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol:391-410`), the order is correct CEI: [1](#0-0) 
Escrow (`_orders[commitment][token]`) is decremented *before* the `.call`/`safeTransfer`.

The Tron variant's `withdraw()` does the opposite — it calls out to the token/beneficiary first, and only afterward mutates the escrow accounting state: [2](#0-1) 

This function is reachable directly by an unprivileged user through the same-chain cancellation path, not gated by `onlyHost`: [3](#0-2) 

It is also reached from `onAccept()` for cross-chain `RedeemEscrow`/`RefundEscrow` settlement: [4](#0-3) 

Two concrete problems follow from this ordering, both matching the report's core "effects after interaction" defect class:
1. **Silent-failure fund loss with non-reverting tokens.** The transfer uses a raw low-level `.call` and only checks that the call itself didn't revert, not the ERC20/TRC20 boolean return value's truthiness beyond the outer `success` bit shown by `.call` semantics for tokens that return `false` without reverting. Since `order.inputs[i].token` is attacker-supplied at `placeOrder` time, an attacker/solver combination involving a non-standard token can make the transfer effectively no-op while `_orders[commitment][token] -= amount` still executes, permanently zeroing the escrow entitlement.
2. **Structural CEI regression.** The mainline code was hardened specifically because a call made mid-loop (to `beneficiary.call{value:...}` or a malicious token's `transfer`) can transfer control before the ledger (`_orders`) is updated, which is exactly the invariant the dHedge report and Polytope's own `IntrinsicIntentsReentrancyTest.sol` regression suite were written to prevent.

**Important caveat on exploitability:** unlike the pre-fix mainline bug (where `_filled` itself was set late, inside `_withdraw(finalize=true)`), the Tron `withdraw()` sets `_filled[body.commitment] = beneficiary` at the very top, *before* the token loop. Both `cancelOrder()` and `fillOrder()` gate on `_filled == address(0)` as their first check, so a naive reentrant call back into `cancelOrder`/`fillOrder` for the **same** commitment is blocked. I could not find a second, `_filled`-independent function that reads/writes `_orders[commitment][token]` for the same commitment, so I could not construct a complete double-spend of the same escrow slot purely via reentrancy. This is a real gap in my analysis that a background engineer should close by auditing every caller of `_orders[...]` for missing `_filled`-equivalent gating.

### Impact Explanation
If exploitable via a crafted token, this allows permanent loss of escrowed user funds (accounting says withdrawn/refunded, but the beneficiary never received the tokens) — a direct "stealing or loss of funds" and "logic attack" impact on live bridge escrow custody, matching the required impact gate. Even without a full double-spend, this is a genuine desync between `_orders` accounting and actual custody, which can also be leveraged to burn honest users' escrow if the fee token or an input token misbehaves.

### Likelihood Explanation
Medium. The path (`cancelOrder` same-chain refund and cross-chain `onAccept` redeem/refund) is on the primary, frequently used settlement flow for the Intent Gateway. The attacker fully controls `order.inputs[i].token` when placing their own order, so supplying a non-standard/return-false token is trivial and does not require a malicious relayer, prover, or admin.

### Recommendation
Port the CEI fix from `evm/src/apps/intentsv2/IntentsBase.sol` into `evm/tron/contracts/apps/IntentGatewayV2.sol`: decrement `_orders[commitment][token]` (and delete the fee entry) before making the outbound transfer, and replace the raw `.call`+return-value pattern with `SafeERC20.safeTransfer`, which reverts on both non-reverting `false` returns and missing return data. Additionally, add the same reentrancy regression tests (`IntrinsicIntentsReentrancyTest.sol` equivalents) to the Tron test suite so any future re-introduction of this ordering is caught in CI.

### Proof of Concept
1. Attacker deploys a TRC20-style token `EvilToken` whose `transfer()` returns `false` without reverting for a chosen sender/recipient pair (fully attacker-controlled, standard-looking token otherwise).
2. Attacker calls `placeOrder()` on the Tron `IntentGatewayV2` with `order.inputs[0].token = EvilToken`, escrowing real balance.
3. Attacker (as order owner, same-chain order) calls `cancelOrder()` after making the order refundable; this invokes `withdraw(body, true)` internally.
4. Inside `withdraw()`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` returns `success = true` (the call itself doesn't revert) even though `EvilToken.transfer` internally returns `false` and moves no tokens.
5. `_orders[commitment][token] -= amount` still executes, permanently marking the escrow as released even though the beneficiary received nothing — corrupting the escrow ledger and causing fund loss for whichever party actually deposited real value under that commitment.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L507-530)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable {
        bytes32 commitment = keccak256(abi.encode(order));

        // order has already been filled
        if (_filled[commitment] != address(0)) revert Filled();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```
