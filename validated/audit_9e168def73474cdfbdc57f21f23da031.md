## Title
Unchecked ERC-20 return value in escrow settlement (`withdraw`/`onAccept`) can silently fail while marking the order as filled - (`evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.sol` (Tron variant) settles escrowed intent funds using raw low-level `.call()` invocations of `IERC20.transfer` instead of `SafeERC20.safeTransfer`. The code only checks that the *call itself* did not revert (`success`), but never decodes/validates the boolean return value that ERC-20 `transfer()` is supposed to return. This is the exact bug class from the external report (Compound V2 `redeem`/`repayBorrowBehalf` not reverting on failure) transplanted onto Hyperbridge's cross-chain intent-escrow settlement path.

### Finding Description
In `withdraw()`, escrowed tokens are released to the beneficiary via: [1](#0-0) 

and protocol dust is swept via the same pattern in `onAccept`'s `SweepDust` branch: [2](#0-1) 

Both only check `success` (i.e., that the target contract call did not revert) and ignore the ABI-decoded boolean return value from `transfer()`. Some ERC-20 implementations (non-reverting on failure, e.g. paused/blacklist-gated tokens, or tokens that simply return `false` instead of reverting when a transfer condition isn't met) will make this call return `success = true` with an encoded `false` payload, exactly mirroring the Compound V2 `redeem`/`repayBorrowBehalf` pattern flagged in the source report.

Crucially, `withdraw()` unconditionally marks the order filled and decrements escrow accounting regardless of whether the underlying transfer actually moved funds: [3](#0-2) [4](#0-3) 

`_filled[body.commitment]` is set before any transfer occurs, and `_orders[body.commitment][token] -= amount` is executed right after the unchecked `.call`. If the token silently returns `false`, the beneficiary receives nothing, but the commitment is now permanently marked filled/refunded and the escrow ledger is decremented as though payment succeeded — the tokens remain stuck in the contract with no accounting path back to the beneficiary, since `withdraw` is only reachable once via `onAccept`/`onGetResponse` for a given commitment.

### Impact Explanation
This is a genuine "loss/lock of funds" bridge-custody bug matching the bounty scope: escrowed cross-chain intent funds move exactly once in the code's intent, but the unchecked return value means that "once" transfer can be a no-op for non-standard tokens while the contract's internal state (`_filled`, `_orders`) still records it as completed. The beneficiary permanently loses access to the escrowed asset with no retry mechanism, since the commitment is already consumed.

### Likelihood Explanation
Likelihood depends on the escrowed token being a non-standard ERC-20 that returns `false` rather than reverting on failed transfers (common among older/compliance-gated tokens). No malicious relayer, prover, or admin is required — this triggers purely from the routine `onAccept`/`onGetResponse` settlement flow processing a legitimate cross-chain redeem/refund message for such a token, i.e., normal usage of the `IntentGatewayV2` contract with one of these tokens as an order input/output asset.

### Recommendation
Replace the raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `withdraw()` and the `SweepDust` branch of `onAccept` with OpenZeppelin's `SafeERC20.safeTransfer`, which decodes and enforces the boolean return value (or absence of one) and reverts on failure, consistent with how other IntentGateway variants (e.g., the non-Tron `IntentsBase.sol`) already use `IERC20(token).safeTransfer(beneficiary, amount)`.

### Proof of Concept
1. Deploy/select a token whose `transfer()` returns `false` on failure instead of reverting (e.g., balance insufficient due to some non-reverting business rule, or a paused/blacklist state that the token contract chooses not to revert on).
2. Create and fill an intent order using this token as an input asset, so it becomes escrowed in `IntentGatewayV2`.
3. Trigger the redeem/refund path so `onAccept` invokes `withdraw(body, ...)` for the commitment.
4. The token's `transfer(beneficiary, amount)` call returns `(true, abi.encode(false))` — `success` is `true` so the check `if (!success) revert TransferFailed();` passes.
5. `_orders[commitment][token] -= amount` executes and `_filled[commitment]` is already set — the commitment is now permanently consumed, but the beneficiary received zero tokens; the escrowed amount is stuck in the contract with no further code path to release it under that commitment.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-667)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-691)
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
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-705)
```text
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
