The main, currently-used `_withdraw` in `evm/src/apps/intentsv2/IntentsBase.sol` follows proper CEI: `_orders[body.commitment][token]` is decremented **before** the external transfer call for every iteration, and `_filled[commitment]` is set before any external calls when `finalize` is true. [1](#0-0) 

However, the Tron variant of the contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, contains the exact broken pattern described in the TokenDrop report: the escrow accounting mapping is decremented **after** the external token transfer, inside `withdraw()`: [2](#0-1) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

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

        _orders[body.commitment][token] -= amount;   // <-- decremented AFTER the external call
        ...
```

`_filled[body.commitment]` is set at the top, which blocks a second **fresh fill** of the same order, but it does not stop this specific `withdraw()` invocation from re-entering: if `token` (or the `beneficiary` for the native-ETH branch) contains a callback (a `receive()`/`fallback()` on the beneficiary address for native transfers, or a token with an ERC-777/ERC-1363/hook-style `transfer`), the callback can re-enter and call `withdraw()` again for the **same** `body.commitment`/`token` before `_orders[body.commitment][token]` has been decremented on the outer call — so the `if (_orders[...] == 0) revert UnknownOrder();` guard still passes and the same escrowed amount is transferred a second time, draining the token beyond what was escrowed for that commitment. This exactly mirrors the TokenDrop bug's broken invariant: the accounting variable that gates future logic is updated only after an external call that can call back in before the update lands.

I was not able to fully verify from the index (i) whether `withdraw()` in the Tron file is reachable from `onAccept()` for both `RedeemEscrow`/`RefundEscrow` paths without an additional guard I might have missed further down in the file, or (ii) whether Tron's specific token standard set makes reentrant ERC-20 `transfer` callbacks realistically deployable there (Tron primarily uses TRC-20, but the contract explicitly still uses `IERC20.transfer` via raw `.call`, so any token with hooks compiled into it would trigger this). Given the ask-only mode, I'm not producing a plan, but wanted to flag that a background agent would need to inspect the full `onAccept` dispatch table in that file to confirm the exact call path and reachability before filing a fix.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-720)
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
```
