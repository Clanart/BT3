### Title
Zero-amount escrow token transfer permanently blocks intent settlement in `withdraw()` - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.withdraw()`, invoked from `onAccept()` on `RedeemEscrow`/`RefundEscrow` messages, iterates over `body.tokens` and unconditionally issues a low-level `token.call(transfer(...))` for every entry, including entries whose `amount` is `0`, and treats any call failure as fatal (`revert TransferFailed()`). This mirrors exactly the Bond Protocol bug class: a reward/settlement amount that legitimately rounds or resolves to zero gets pushed through a raw ERC-20 transfer, and if the token reverts on a zero-value transfer, the whole settlement call reverts.

### Finding Description
In `withdraw()`: [1](#0-0) 

For each `body.tokens[i]`, the code checks only that `_orders[commitment][token] != 0` (i.e., the escrow entry exists) — it does **not** check that the per-token `amount` about to be transferred is non-zero: [2](#0-1) 

Contrast this with the fee leg immediately below it, which *does* guard against zero: [3](#0-2) 

The asymmetry is the smoking gun: the developers already knew a zero-value payout needs to be skipped (they did it for `fees`), but did not apply the same guard to the token-payout loop. If `token` is an ERC-20 that reverts on `transfer(_, 0)` (a documented pattern for several real-world tokens), then a zero-amount leg in `body.tokens` causes `success == false`, `revert TransferFailed()` fires, and the **entire** `withdraw()` call — and therefore the whole `onAccept` message execution — reverts.

Because `withdraw()` is called from `onAccept`, which is the terminal handler for an incoming ISMP `RedeemEscrow`/`RefundEscrow` POST request, a revert here means:
- The message delivery fails on the destination, so the request is never marked as successfully processed for this `commitment`.
- `_filled[body.commitment]` is never durably set (the write happens but the whole tx including that write is rolled back).
- Every other token amount bundled in the *same* order (even non-zero, otherwise-fine legs) is blocked in the same atomic call, so a single zero-amount leg locks the entire order's escrowed funds.
- Re-delivery of the same request will hit the exact same zero amount and revert again — there is no way to "skip" the zero leg once the request body is fixed, since the body was already committed/hashed on the source chain. This is a structural dead end, exactly like the `epochTransitionReward == 0` deadlock described in the seed report before the fix added a zero check plus a setter.

Where can a zero `amount` originate? `body.tokens[i].amount` is decoded directly from the source-chain-authenticated request body (`authenticate(incoming.request)` only checks source/module authenticity, not value ranges), so it reflects whatever amount was recorded in escrow at order-creation/fill time on the source chain. Any downstream computation that can legitimately produce a zero remainder for one leg of a multi-token order (e.g., dust after fee/slippage deduction, a partially-filled multi-asset order, or a solver-crafted order with an intentionally tiny/zero secondary output) reaches this code path with no defense.

### Impact Explanation
This falls squarely under "stealing or loss of funds" / "transaction manipulation" in the Hyperbridge bounty scope: escrowed user funds tied to an intent order become permanently unredeemable/unrefundable once a zero-amount leg is present, because the settlement message can never be successfully executed. This is a fund-lock on the bridge's escrow custody path, not a peer/relayer/prover assumption issue — it's a pure contract-logic defect triggered by ordinary message content.

### Likelihood Explanation
Likelihood is moderate-to-high: any ERC-20 that reverts on a zero-value `transfer` (a known, non-exotic behavior in various tokens) combined with any order/fill flow that can produce a zero remainder for one of multiple escrowed token legs triggers this permanently. No malicious relayer, prover, or governance actor is required — a legitimate user order routed through a standard message-delivery flow is sufficient once the zero-amount condition arises.

### Recommendation
Mirror the existing fee-leg pattern: skip the transfer (and the associated `_orders` decrement) when `amount == 0`, instead of unconditionally calling `transfer`:
```solidity
if (amount > 0) {
    if (token == address(0)) {
        (bool sent,) = beneficiary.call{value: amount}("");
        if (!sent) revert InsufficientNativeToken();
    } else {
        (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
        if (!success) revert TransferFailed();
    }
}
_orders[body.commitment][token] -= amount;
```

### Proof of Concept
1. Create/fill an intent order whose `body.tokens` array includes at least two entries, where one entry's `amount` resolves to `0` (e.g., via a fee/slippage computation on the source chain that zeroes out a dust leg, or a solver constructing a multi-token order with a zero secondary payout).
2. Deploy (or use) an ERC-20 for that zero-amount leg that reverts on `transfer(to, 0)`.
3. Have the ISMP `RedeemEscrow`/`RefundEscrow` request delivered to `IntentGatewayV2.onAccept()`.
4. `withdraw()` reaches the zero-amount leg, calls `token.call(transfer(beneficiary, 0))`, the token reverts, `success == false`, and `TransferFailed()` reverts the whole `withdraw()`/`onAccept()` call.
5. Because the request body (and thus the zero amount) is fixed by the source-chain commitment, every future delivery attempt for this commitment fails identically — the escrowed funds for **all** tokens in that order are permanently locked, with no admin path to force settlement in the current code (unlike `SweepDust`/governance recovery routes present elsewhere in the contract, which are not invoked by this failure path).

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-714)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```
