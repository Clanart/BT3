Based on my investigation, I was not able to conclusively trace the full cross-chain dispatch path in `ExtrinsicIntents.sol` (I ran out of tool budget before reading that file), so I cannot fully confirm the exploit chain end-to-end. I'll present what I *did* confirm with code citations, flag the one unverified link explicitly, and let that stand as the answer rather than overstating certainty.

### Title
Escrow withdrawal underflows and permanently locks funds when the requested amount exceeds the recorded escrow balance - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`IntentsBase._withdraw` (and its duplicate in `evm/tron/contracts/apps/IntentGatewayV2.sol`) guards the escrow-release subtraction with `if (escrowed == 0) revert UnknownOrder();` instead of `if (escrowed < amount) revert(...)`, before doing `_orders[body.commitment][token] = escrowed - amount;`. [1](#0-0) 

The equivalent function in the Tron fork is identical in structure: [2](#0-1) 

### Finding Description
This is the same broken-invariant class as the `xERC4626` report: a value used to gate a subtraction (`escrowed`) is checked for "non-zero" rather than "large enough," so if the amount requested for release (`amount`, taken from the decoded `WithdrawalRequest.tokens[i].amount`) ever exceeds the actual escrow balance on record (`_orders[body.commitment][token]`), the subtraction underflows. Because this code targets Solidity `^0.8`, the subtraction is checked arithmetic and reverts (a panic), rather than silently wrapping — but the practical effect mirrors the xERC4626 bug: the withdrawal/refund/release call can never succeed for that order, and the escrowed tokens become permanently stuck, since `_withdraw` is the single code path used by fill-settlement (`EscrowReleased`), same-chain cancel, and both cross-chain refund/redeem flows (`onAccept` / `onGetResponse`).

A concrete way `escrowed` and `amount` could diverge is a fee-on-transfer / deflationary ERC‑20 used as an order input on the source chain. The team is clearly aware of this general class of discrepancy — there's a same-chain-specific regression test asserting escrow bookkeeping matches the *actual* received balance rather than the nominal transfer amount: [3](#0-2) 

and the same-chain finalize path in `IntrinsicIntents._fillSameChain` deliberately reads the live stored escrow value rather than a nominal amount to close a previously-found rounding-dust lock bug: [4](#0-3) [5](#0-4) 

I could not confirm whether the **cross-chain** redeem/refund message (`ExtrinsicIntents.sol`, which dispatches `RedeemEscrow`/`RefundEscrow` back to the source chain) populates `WithdrawalRequest.tokens[i].amount` from the nominal `order.inputs[i].amount` (part of the cross-chain-committed `Order` struct) or from the source chain's actually-recorded escrow value. If it uses the nominal `order.inputs[i].amount` — which is the only value the destination chain has visibility into, since the source-side actual-received amount for a fee-on-transfer token is chain-local state — then any input token that takes a transfer fee would make `escrowed < amount` on the source chain at redemption time, triggering the underflow and permanently locking the user's/solver's escrow for that order.

### Impact Explanation
If the underflow path is reachable, it results in permanent fund lock (no theft) for the affected order's escrow — the solver cannot claim the fill, and the user cannot get a refund, because every call to `withdraw`/`_withdraw` for that commitment reverts. This matches "Discard: generic gas or network DoS" only if it were unreachable by design; if reachable via ordinary fee-on-transfer tokens (no malicious relayer/prover/admin required, no front-running needed), it is an availability/fund-lock impact within the bounty's accepted categories ("stealing or loss of funds" via permanent lock).

### Likelihood Explanation
Low-to-Medium and **unconfirmed**. It requires (a) an order using a fee-on-transfer/deflationary token as input, and (b) the cross-chain dispatch path using the nominal order amount rather than the recorded escrow amount when building the redeem/refund message — a detail I was unable to verify in `ExtrinsicIntents.sol` within the available tool budget. The presence of a same-chain-only fix/test for this exact discrepancy class is suggestive but not conclusive that the cross-chain path is unpatched.

### Recommendation
- Audit `ExtrinsicIntents.sol` to confirm the source of `body.tokens[i].amount` for `RedeemEscrow`/`RefundEscrow` dispatches; it must reflect the chain-local recorded `_orders[commitment][token]` value, not the nominal `order.inputs[i].amount`, when the input token can be fee-on-transfer or rebasing.
- In `IntentsBase._withdraw` (and the Tron duplicate), replace `if (escrowed == 0) revert UnknownOrder();` with an explicit `if (escrowed < amount) revert InsufficientEscrow();` check before the subtraction, and prefer releasing `min(escrowed, amount)` or the full recorded `escrowed` on finalize, matching the pattern already used in `IntrinsicIntents._fillSameChain` for the final fill.
- Add a fee-on-transfer-token regression test for the cross-chain redeem/refund path analogous to the existing same-chain `FeeOnTransferToken` test.

### Proof of Concept
Not independently reproduced — this requires confirming the `ExtrinsicIntents.sol` dispatch body construction, which I was unable to inspect before running out of tool calls. The reasoning above is based on:
1. The vulnerable guard-before-subtraction pattern confirmed in both `IntentsBase.sol` and the Tron `IntentGatewayV2.sol`.
2. Confirmed test evidence that the team treats "escrowed nominal vs. actual-received" as a real discrepancy class for the same-chain path only.

Given this residual uncertainty on the exploit trigger, treat this as a **candidate** finding requiring verification of `ExtrinsicIntents.sol` before filing, rather than a fully proven vulnerability.

### Citations

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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1547-1557)
```text
    /// @notice Verifies that rounding dust from integer division in partial fills
    /// is not permanently locked. The final solver completing the order should
    /// receive the full remaining escrow balance rather than a truncated amount.
    function testPartialFill_RoundingDustReleasedToFinalSolver() public {
        // Choose amounts that produce rounding truncation:
        // input = 100 USDC (100e6), output = 3 DAI (3e18)
        // Each of 3 solvers fills 1 DAI. Proportional release per fill:
        //   100e6 * 1e18 / 3e18 = 33333333 (truncated from 33333333.33...)
        // Without fix: 3 * 33333333 = 99999999, leaving 1 unit locked.
        // With fix: final solver gets remaining balance = 100e6 - 2*33333333 = 33333334
        uint256 inputAmount = 100 * 1e6; // 100 USDC
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2491-2498)
```text
        bytes32 commitment = keccak256(abi.encode(order));

        assertEq(
            intentGateway._orders(commitment, address(fot)),
            gatewayReceived,
            "Escrow should match actual received after double transfer fee"
        );
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
