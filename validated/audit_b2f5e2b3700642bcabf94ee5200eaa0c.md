## Finding: Unchecked ERC-20 return value in `IntentGatewayV2.withdraw()` (Tron port)

### Title
Escrow finalized and accounted-released without verifying actual token transfer success - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron-chain port of `IntentGatewayV2`'s internal `withdraw()` function releases escrowed order funds using a raw low-level `.call` to the ERC-20 `transfer` selector, but only checks that the *call itself* did not revert — it never decodes or verifies the boolean return value that ERC-20 `transfer` is supposed to produce. This differs from every sibling contract in the same codebase (`IntentsBase._withdraw`, `HyperFungibleToken`, `WrappedHyperFungibleToken`, etc.), which all use OpenZeppelin's `SafeERC20.safeTransfer`, correctly reverting on a `false` return.

### Finding Description
`withdraw()` is the fund-release routine invoked from both the `RedeemEscrow`/`RefundEscrow` cross-chain settlement path (`onAccept`) and the same-chain/GET-response cancel path (`onGetResponse`, `cancelOrder`): [1](#0-0) 

The relevant transfer logic:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```
`success` here only reflects whether the external call reverted — not whether the token's `transfer()` logic itself returned `true`. Any ERC-20 implementation that returns `false` on failure instead of reverting (a well-documented non-compliance pattern in the ERC-20 ecosystem) will make this check pass even though no tokens moved.

Critically, this unchecked transfer happens *before* the escrow accounting is finalized in the same function: [2](#0-1) 
- `_filled[body.commitment] = beneficiary;` is set unconditionally at the top of `withdraw()`.
- `_orders[body.commitment][token] -= amount;` is decremented right after the unchecked call, regardless of whether the transfer actually succeeded.

The same unchecked-call pattern also appears in the `SweepDust` handler and the fee-token payout inside `withdraw()`: [3](#0-2) [4](#0-3) 

Contrast with the properly-guarded mainline logic that uses `SafeERC20.safeTransfer`, which reverts the whole transaction if the ERC-20 return value is `false`: [5](#0-4) 

Because `_filled` is set and `_orders[...]` is zeroed regardless of the real transfer outcome, once this path executes there is no retry: any subsequent legitimate withdrawal for the same commitment/token reverts with `UnknownOrder()` since `_orders[body.commitment][token]` is now `0`.

### Impact Explanation
This is a real fund-loss vector on the Tron deployment: an order whose input token is a non-reverting-on-failure ERC-20 can have its escrow marked "released"/"refunded" (`EscrowReleased`/`EscrowRefunded` emitted, `_filled` set, `_orders` zeroed) while the beneficiary (solver on `RedeemEscrow`, or user on `RefundEscrow`/cancel) receives nothing. The funds are irrecoverably stuck in the contract, and the false success state is baked into on-chain history and the ISMP message flow, meeting the "false state acceptance" / "loss of funds" impact classes for this bounty — this triggers on the normal, permissionless settlement/cancel flow with no malicious relayer, prover, or admin required, only a specific (non-conforming) ERC-20 used as an order's input token.

### Likelihood Explanation
Requires that the escrowed input token deviate from the strict `transfer() -> bool` success/revert contract (returns `false` instead of reverting on failure) — a known class of real ERC-20 implementations. Since `placeOrder` accepts an arbitrary `token` address chosen by the order creator with no whitelist enforced in this contract, this condition is reachable without any privileged actor; it only needs the transfer to legitimately fail once (e.g., a paused/blacklist-style token, or any edge case where the token's internal logic returns `false`).

### Recommendation
Replace all raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `evm/tron/contracts/apps/IntentGatewayV2.sol` (in `withdraw()`, `onAccept`'s `SweepDust` branch, and the transaction-fee payout) with OpenZeppelin's `SafeERC20.safeTransfer`, matching the pattern already used in `IntentsBase._withdraw` and the `HyperFungibleToken`/`WrappedHyperFungibleToken` contracts, so that a failed transfer reverts the entire state mutation (escrow decrement and `_filled` marking) instead of silently finalizing it.

### Proof of Concept
1. Deploy or use an ERC-20 token `T` whose `transfer()` implementation returns `false` (rather than reverting) under some failure condition (e.g., a paused state or an internal check).
2. User places a cross-chain order via `placeOrder` on the source chain escrowing `T`.
3. Solver fills the order on the destination chain; the resulting `RedeemEscrow` ISMP message is delivered to `onAccept` on the source chain, calling `withdraw(body, false)`.
4. If token `T` is in the failure condition when `withdraw` executes, `token.call(...)` succeeds (no revert) but the underlying `transfer` returns `false`; the code does not check this.
5. `_orders[commitment][T] -= amount` still executes, `_filled[commitment]` is set, and `EscrowReleased` is emitted — yet the solver's balance of `T` is unchanged.
6. The solver has no path to reclaim the funds: any further call into `withdraw` for this commitment/token reverts with `UnknownOrder()` since the escrow bookkeeping shows zero remaining.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-672)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
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
