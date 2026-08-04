## Analog Found: Unchecked ERC-20 return value in `IntentGatewayV2` escrow withdrawal (Tron deployment)

### Title
Escrow withdrawal treats failed (non-reverting, `false`-returning) ERC-20 transfers as successful settlement - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2` still contains the exact bug class from the external report: it does not use `SafeERC20`. Instead of `safeTransfer`, `withdraw()` and the `SweepDust` handler in `onAccept()` perform a raw low-level `.call` with the `IERC20.transfer` selector and only check the call's `success` boolean — never the ERC-20 return data. This is worse than the reported `transferFrom`-without-revert-check bug because it affects the actual **escrow release/refund path**, not just a deposit dust-refund.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, `withdraw()` marks the order finalized and decrements escrow accounting *before/alongside* a token payout that is not safely verified: [1](#0-0) 

The transfer to the beneficiary is done via:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
``` [2](#0-1) 

`success` here only reflects whether the low-level call itself reverted — it says nothing about whether the token's `transfer()` function actually returned `true`. Tokens that signal failure by returning `false` instead of reverting (the same broken-ERC20 category cited in the external `EncryptedERC` report) will make this call return `success == true` with no tokens actually delivered. The same unchecked pattern is repeated for the `TRANSACTION_FEES` payout and in the `SweepDust` branch of `onAccept`: [3](#0-2) [4](#0-3) 

Because `_filled[body.commitment] = beneficiary` is set and `_orders[body.commitment][token] -= amount` is executed regardless of whether the token silently failed, the order is irrevocably marked settled (`EscrowReleased`/`EscrowRefunded` emitted) while the beneficiary receives nothing and the tokens remain stuck in the gateway with no accounting path left to reclaim them (escrow balance already zeroed, `_filled` already non-zero blocks any retry).

By contrast, the current mainline EVM implementation (`evm/src/apps/intentsv2/IntentsBase.sol`) already fixed this exact issue by adopting `SafeERC20.safeTransfer`/`safeTransferFrom` throughout `_withdraw()` and `_sweepDust()`: [5](#0-4) [6](#0-5) 

This confirms the Tron contract is a stale/unpatched analog of the same bug class the external report addressed, sitting in a bridge custody / intent-settlement critical path rather than a simple deposit function.

### Impact Explanation
This directly matches the bounty's "stealing or loss of funds" and "false proof/state acceptance" categories: escrow tokens can be permanently locked in the `IntentGatewayV2` (Tron) contract while the order-completion state (`_filled`, `_orders`, and the `EscrowReleased`/`EscrowRefunded` events relied on by off-chain indexers and the cross-chain GET-based cancellation check) falsely reflects that the beneficiary was paid. Any non-standard ERC-20 configured as an order's input/fee token that returns `false` rather than reverting on failure (e.g., due to insufficient allowance/balance edge cases in certain token implementations, or blacklist/pausable tokens) triggers this without any privileged actor — the withdrawal path is invoked automatically by `onAccept`/`onGetResponse` once a legitimate RedeemEscrow/RefundEscrow/GET response is processed, so an ordinary solver or user interacting with such a token suffers silent fund loss.

### Likelihood Explanation
Likelihood is constrained to gateway deployments that whitelist or accept a non-standard ERC-20 (one that returns `false` instead of reverting on failure) as an order input or fee token; for strictly-compliant tokens (which revert), the `require`-style `if (!success) revert` still catches call-level reverts. However, since token selection for orders is attacker/user-controlled (any ERC-20 address can be placed as `order.inputs[i].token`), a user or solver can deliberately choose or interact with such a token to trigger the silent-failure state, making this exploitable without any relayer/prover/governance compromise — satisfying the "unprivileged attacker" requirement.

### Recommendation
Replace all raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `evm/tron/contracts/apps/IntentGatewayV2.sol` (`withdraw()`, the `TRANSACTION_FEES` payout, and the `SweepDust` branch of `onAccept`) with OpenZeppelin's `SafeERC20.safeTransfer`, mirroring the fix already applied in `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw` and `_sweepDust`. This ensures both call-level revert and false-return-value failures cause the whole withdrawal to revert, preventing `_filled`/`_orders` state from advancing without an actual token transfer.

### Proof of Concept
1. Deploy/register a non-standard ERC-20 token (returns `false` on failed `transfer`, e.g., due to a paused/blacklist state) as an order's input token on the source chain, and complete a normal cross-chain intent flow so tokens are escrowed in `evm/tron/contracts/apps/IntentGatewayV2.sol`.
2. Trigger the RedeemEscrow/RefundEscrow flow so `onAccept` invokes `withdraw(body, ...)` at line 682.
3. Before delivery, cause the destination-side balance/allowance condition of the token to make its `transfer()` return `false` (not revert) — e.g., token owner pauses it or the beneficiary is blacklisted.
4. Observe: `withdraw()`'s `token.call(...)` returns `success == true` (call didn't revert) even though the token's internal `transfer` logic returned `false` and moved no funds; `_orders[commitment][token]` is decremented, `_filled[commitment]` is set, and `EscrowReleased`/`EscrowRefunded` is emitted — permanently finalizing the order while the beneficiary receives zero tokens, which remain stranded in the gateway contract.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-673)
```text
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L586-591)
```text
            if (token == address(0)) {
                (bool sent,) = req.beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(req.beneficiary, amount);
            }
```
