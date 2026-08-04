Based on my investigation, I found a concrete local analog: `_orders[commitment][token]` is a per-**token** escrow bucket (not per-leg), and `_fillSameChain` in `IntrinsicIntents.sol` reads the *entire remaining balance* of that bucket instead of the proportional share whenever a single leg's cumulative fill reaches its `totalRequired`, even though the overall order can still have other unfilled legs pending in the same bucket.

### Title
Same-chain partial fill drains other legs' escrow when input tokens repeat across order legs - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`_orders` is keyed only by `(commitment, token address)`, aggregating escrow across *all* input legs that happen to use the same token [1](#0-0) . `_fillSameChain` computes the amount of escrow to release for leg `i`, and when that leg's output is completed (`amountFilled == totalRequired`) it releases `_orders[commitment][token]` in full rather than the leg's proportional share [2](#0-1) . If two (or more) `order.inputs[i]` entries share the same token address — which nothing in `placeOrder`/`Order` validation appears to forbid — completing the first output leg pulls out the combined escrow balance backing *both* legs, not just its own share.

### Finding Description
The escrow bookkeeping model is per-token, not per-leg: `mapping(bytes32 => mapping(address => uint256)) public _orders` [1](#0-0) . When a solver completes leg `i` (`amountFilled == totalRequired` for that output token), the code takes a shortcut and sets `escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))]` — the full current balance in the bucket — instead of computing `order.inputs[i].amount` proportional to `fillAmount/totalRequired` as it does for genuinely partial legs [2](#0-1) . This value is then handed to `_withdraw`, which decrements `_orders[commitment][token]` by exactly that amount and transfers it to the solver who filled leg `i` [3](#0-2) .

If `order.inputs` contains two entries with the same token (e.g. the user escrows USDC against two different output legs, or one input funds two output pairs), each maps to the same `_orders[commitment][token]` slot. A solver who completes only the first output leg — while the second leg's output is still unfilled — receives the entire combined escrow for that token, including the portion that was supposed to back the second (still-open) leg. The remaining leg's `_partialFills` accounting still expects escrow to be available, but `_orders[commitment][token]` is now zero or reduced, so:
- The rightful second solver's later fill either reverts with `UnknownOrder()` in `_withdraw` (escrow == 0) [4](#0-3) , locking the user's remaining output requirement unfulfillable, or
- A subsequent `_cancelSameChain` call sees deflated/zero `_orders[commitment][token]` and refunds less than the user is actually owed, or reverts with `UnknownOrder` entirely if that token's bucket hit zero even though other tokens still need refunding [5](#0-4) .

This mirrors the reported Sui bug class exactly: a shared pooled balance (`NativePool::pending` there, `_orders[commitment][token]` here) is consumed in full by one actor's action without accounting for other legitimate claims still pending against that same pool, causing another party to be unable to complete their rightful withdrawal/fill.

### Impact Explanation
This is an unauthorized fund extraction / loss-of-funds path reachable by any unprivileged solver: an attacker filling the first output leg of a multi-leg order that shares an input token across legs can capture escrow that should remain reserved for the other leg, either overpaying themselves relative to the fill they actually provided, or stranding the user's remaining escrow such that it can never be released to a legitimate second solver and can potentially become unrefundable via `_cancelSameChain`'s `UnknownOrder` revert. This is a public entrypoint (`fillOrder`/`cancelOrder`), requires no privileged role, relayer, or prover collusion, and directly manipulates fund custody/settlement — matching the "stealing or loss of funds" and "transaction manipulation" bounty categories.

### Likelihood Explanation
Exploitability depends on whether the protocol/SDK enforces "unique token per leg" when constructing an `Order`. I could not verify such a constraint anywhere in `IntentsBase.sol`, `IntrinsicIntents.sol`, or `ExtrinsicIntents.sol` — no validation function rejects duplicate `order.inputs[].token` entries. If the off-chain SDK order-builder happens to always produce unique tokens per leg, the on-chain contract still has no defense-in-depth check, so a user or malicious order-placer who crafts a raw order directly against the contract (bypassing the SDK) can trigger this. Given the contract accepts arbitrary `Order` structs at `placeOrder`/`fillOrder` without deduplication checks, likelihood is assessed as plausible but contingent on multi-leg orders with a repeated input token, which I could not fully rule in or out as a supported/expected order shape from the available code.

### Recommendation
Track escrow per `(commitment, leg index)` rather than per `(commitment, token)`, or explicitly validate at `placeOrder` time that no two input legs share the same token address so the token-keyed aggregation is safe. Alternatively, change the "leg complete" shortcut in `_fillSameChain` to release the proportional `order.inputs[i].amount` rather than the entire token bucket, reserving the full-balance sweep only for the case where the *whole order* (`isFullyFilled`) is complete.

### Proof of Concept
1. User places a same-chain order with `order.inputs = [{token: USDC, amount: 600}, {token: USDC, amount: 400}]` and two output legs `order.output.assets = [{token: DAI, amount: 600e18}, {token: WETH, amount: 1e18}]`. Both input legs are escrowed into the same `_orders[commitment][USDC]` bucket, totaling 1000 USDC [1](#0-0) .
2. Solver A calls `fillOrder` providing only the DAI leg in full (`600e18`). In the loop, leg 0 reaches `amountFilled == totalRequired`, so `escrowedAmount = _orders[commitment][USDC]` = the full 1000 USDC bucket (not the proportional 600) [2](#0-1) . `_withdraw` transfers all 1000 USDC to Solver A and zeroes the bucket [6](#0-5) .
3. `isFullyFilled` is false (WETH leg unfilled), so the order stays open awaiting the WETH leg [7](#0-6) .
4. Solver B later tries to fill the WETH leg; when `_withdraw` looks up `_orders[commitment][USDC]` for the second leg's escrow release, it is now `0`, and the call reverts with `UnknownOrder()` [4](#0-3) , or the user's `_cancelSameChain` attempt returns 0 for that token/`UnknownOrder` despite still legitimately owing WETH-leg escrow [8](#0-7) .

Note: I could not confirm from the indexed code whether upstream SDK/off-chain order construction disallows duplicate `token` entries across `order.inputs`; this uncertainty should be resolved by checking `evm/src/apps/IntentGatewayV2.sol`'s `placeOrder` validation logic and the SDK's order builder directly, which may not be fully covered by the index.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-122)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L139-141)
```text
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L169-181)
```text
        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
        if (!hasEscrow) revert UnknownOrder();
```
