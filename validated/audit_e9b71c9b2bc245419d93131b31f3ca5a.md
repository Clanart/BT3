### Title
Escrow settlement in `IntentGatewayV2.withdraw` accepts silently-failing ERC20 transfers as successful, permanently locking beneficiary funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` on the Tron EVM build releases escrowed intent tokens using a raw low-level `.call` to `IERC20.transfer`, checking only that the call itself did not revert (`success`) rather than decoding and validating the boolean return value the ERC20 standard mandates. Non-compliant tokens that return `false` instead of reverting on transfer failure will cause this function to treat the transfer as successful, permanently marking the order as filled/refunded and decrementing internal escrow accounting even though no tokens were actually delivered to the beneficiary.

### Finding Description
`withdraw()` is the shared settlement routine invoked from `onAccept()` for both `RedeemEscrow` (fill payout) and `RefundEscrow` (cancellation refund) message kinds: [1](#0-0) 

Inside `withdraw()`, for every escrowed ERC20 input, the contract performs: [2](#0-1) 

and for accrued transaction fees: [3](#0-2) 

In both cases the code does `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` and only inspects the low-level `success` boolean returned by `.call`. Per the ERC20 spec, `transfer` is only required to return a boolean indicating success — it is not required to revert on failure. A low-level `.call` only reports `success = false` when the callee itself reverts; if the token contract executes normally and simply returns `false` (e.g. 0x Protocol Token-style non-compliant tokens, or any token that decides not to move balance under certain conditions), `success` is `true` even though the beneficiary received nothing. This is the exact bug class from the referenced report (`DepositPaymaster` ignoring ERC20 return values), reproduced locally instead of using `SafeERC20.safeTransfer`, which the rest of the codebase (e.g. `IntentGatewayV2.sol` in `evm/src/apps`, `HyperFungibleToken.sol`, `WrappedHyperFungibleToken.sol`) consistently uses via `SafeERC20`.

Critically, `withdraw()` unconditionally advances state regardless of the actual transfer outcome: [4](#0-3) [5](#0-4) 

`_filled[body.commitment]` is set to the beneficiary and `_orders[body.commitment][token]` is decremented before/regardless of whether tokens actually moved. There is no re-entry mechanism to retry withdrawal once `_filled` is set, so a silently-failed transfer results in tokens stuck in the gateway contract while the settlement is recorded as final.

### Impact Explanation
This causes real loss/lock of bridged escrow funds without a malicious peer, relayer, or admin: any intent whose input/fee token happens to be a non-reverting-on-failure ERC20 (the exact same class of tokens called out in the seed report) will have its cross-chain settlement finalized (`EscrowRefunded`/`EscrowReleased` emitted, `_filled` set) while the beneficiary never receives the funds, and the escrow accounting is decremented as if it did. This matches the bounty's "stealing or loss of funds" and "false state acceptance" impact categories, since the on-chain settlement record diverges from actual token custody with no recovery path.

### Likelihood Explanation
The path is reachable through the normal, unprivileged intent fill/cancel flow — a user creates and fills an intent using a non-standard ERC20 as input or fee token; no attacker privilege, malicious relayer, or compromised prover is required, only the token's ordinary behavior on transfer failure. The `onAccept` caller check (`onlyHost`) and `authenticate()` only validate that the request comes from the correct paired gateway via Hyperbridge, not that the underlying token transfer succeeded, so these guards do not stop the false-success bookkeeping.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` (and the `SweepDust` handler using the same pattern) with OpenZeppelin's `SafeERC20.safeTransfer`, consistent with the non-Tron `evm/src/apps/IntentGatewayV2.sol` and `HyperFungibleToken`/`WrappedHyperFungibleToken` contracts, which already `using SafeERC20 for IERC20` and decode/validate the actual boolean return data (or require a revert) before mutating `_filled`/`_orders` state.

### Proof of Concept
1. Deploy an ERC20 whose `transfer` returns `false` (without reverting) when, e.g., the contract is paused or the recipient is blacklisted — mirroring the 0x Protocol Token pattern cited in the seed report.
2. Create and fill a cross-chain intent on `IntentGatewayV2` (Tron build) using this token as an input/fee token; escrow tokens into the gateway per the normal flow.
3. Trigger the corresponding `RedeemEscrow`/`RefundEscrow` message so `onAccept` invokes `withdraw()`.
4. Arrange (via the token's own logic, e.g. pausing itself before settlement) for `transfer` to return `false`; the low-level `.call` still reports `success = true` since the token contract does not revert.
5. Observe: `_filled[commitment]` is set and `_orders[commitment][token]` is decremented, `EscrowReleased`/`EscrowRefunded` is emitted, yet `IERC20(token).balanceOf(beneficiary)` is unchanged — the tokens remain stuck in the gateway with no code path left to retry or reclaim them.

### Citations

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
