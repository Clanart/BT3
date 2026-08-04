## Analysis Result [1](#0-0) 

### Title
Atomic multi-token escrow release in `IntentGatewayV2.withdraw` lets a single reverting transfer permanently lock all other escrowed tokens/fees for an order - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Rubicon `M-27` bug class is "a single non-zero-address-unchecked token transfer to a fee recipient can revert and permanently DoS fund release." `IntentGatewayV2.withdraw()` reproduces and amplifies this exact broken invariant: it iterates over every escrowed token for an order commitment and the transaction-fee token in one atomic call, with no per-token isolation, retry, or address validation, so a single reverting `token.call(...)` permanently blocks release of *all* other unrelated escrowed assets tied to that commitment.

### Finding Description
`withdraw(WithdrawalRequest memory body, bool isRefund)` [1](#0-0)  is the single settlement path used both for `RedeemEscrow` (paying a filler) and `RefundEscrow` (returning funds to the order owner), reached via `onAccept` [2](#0-1) . It:

1. Loops over `body.tokens[]` and, for each ERC20, does `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))`, reverting the *entire* function with `TransferFailed()` if any single transfer's low-level call fails.
2. Then attempts to transfer the accumulated `TRANSACTION_FEES` bucket to the same `beneficiary`, again reverting the whole call on failure.

Just like the Rubicon `feeTo` bug, there is no isolation between the "fee" leg and the "principal" leg, and no defensive handling of a token that reverts on transfers to a particular address (blacklist, zero-address, paused token, non-standard `returns (bool)` semantics, etc.). Because both the principal-token loop and the fee-token transfer are combined atomically with a hard revert on any single failure, one problematic token/beneficiary pairing anywhere in `body.tokens` (or in the fee token) blocks disbursement of every other unrelated token amount recorded under the same order `commitment` — even tokens that have no transfer restriction at all. There is no fallback path (e.g., escrow-per-token release, pull-payment, or skip-and-continue) once a single leg fails.

### Impact Explanation
This falls squarely under "stealing or loss of funds" / "logic attacks" in the impact gate: legitimate escrowed principal that is otherwise transferable becomes permanently unrecoverable because it is bundled atomically with a transfer that can revert. Both the `RedeemEscrow` (filler payout) and `RefundEscrow` (owner refund) code paths funnel through this same `withdraw` function, so the failure mode applies to real settlement of user/solver funds, not a peripheral fee sweep.

### Likelihood Explanation
Exercising this requires only that one of the tokens configured in `order.inputs` (chosen at order-creation time, a user-controlled value) or the fee token behaves like `USDC`/similar transfer-restricted tokens toward the resolved `beneficiary` — the exact condition the seed report demonstrates is realistic and already confirmed by the code-423n4 judge. No relayer, prover, or admin compromise is needed; it's a direct consequence of the atomic, unguarded, low-level-call settlement logic that ships in production.

### Recommendation
Decouple the fee-token transfer from the principal-token loop, and make each token transfer in `body.tokens` independently recoverable (e.g., credit a per-token/per-beneficiary claimable balance instead of reverting the whole batch on a single failed leg), so a problematic token or blacklisted beneficiary cannot block release of unrelated, non-problematic escrowed assets.

### Proof of Concept
1. An order is placed with `order.inputs = [TokenA (normal ERC20), TokenB (USDC-like, reverts on transfer to disallowed addresses)]` and `order.fees > 0`.
2. The order is filled/expires and `RedeemEscrow`/`RefundEscrow` is dispatched to `onAccept`, calling `withdraw(body, ...)`.
3. If `beneficiary` (filler address or `order.user`) is later blacklisted on TokenB (or TokenB simply reverts for any policy reason), the loop's `token.call(...)` for TokenB returns `success = false`, hitting `revert TransferFailed()` at [3](#0-2) .
4. The entire transaction reverts — TokenA (which had no transfer restriction) and the accumulated `TRANSACTION_FEES` are never released, and `_orders[commitment][...]` is never decremented, permanently locking otherwise-healthy escrowed funds for that commitment with no alternate recovery code path in the contract.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-721)
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

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
    }
```
