Confirmed: `placeOrder`, `dispatchWithFeeToken` (not fully re-checked, but escrow inflows) and the destination-side dust sweep in `IntentGatewayV2.sol` (Tron variant) consistently use OpenZeppelin's `SafeERC20.safeTransferFrom`/`safeTransfer` for **inbound** token movement, but the two functions that move escrowed value back **out** to a beneficiary — `withdraw()` (called from `onAccept` for `RedeemEscrow`/`RefundEscrow` and from `onGetResponse`) and the `SweepDust` branch of `onAccept` — use a raw low-level `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only check that the call did not revert (`success`), never inspecting the returned `bool`.

### Title
Unchecked ERC20 return value in `IntentGatewayV2.withdraw`/`SweepDust` (Tron) causes silent fund loss on escrow release/refund - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron port of `IntentGatewayV2` escrows user funds in `placeOrder` using `SafeERC20.safeTransferFrom`, but releases them in `withdraw()` (lines 682-721) and in the `SweepDust` handler (lines 652-673) using a bare `.call()` to the ERC20 `transfer` selector, checking only that the external call did not revert.

### Finding Description
`withdraw()` is the single exit path for escrowed intent funds, invoked from `onAccept` for both `RedeemEscrow` (pay the solver) and `RefundEscrow` (pay the user back), and from `onGetResponse` after a source-side cancellation proof: [1](#0-0) 

The transfer-out logic is:
```
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```
This only verifies the low-level call did not revert; it never decodes/checks the returned `bool`. Many real ERC20 tokens (older USDT-style implementations, some non-standard tokens deployed to TRC20/EVM-compatible chains) return `false` on a failed transfer instead of reverting. In that case `success` is `true` even though no tokens moved.

Immediately after this unchecked transfer, the function unconditionally finalizes the withdrawal:
```
_orders[body.commitment][token] -= amount;
...
if (isRefund) { emit EscrowRefunded(...) } else { emit EscrowReleased(...) }
```
and, in `onAccept`/`withdraw`, `_filled[body.commitment] = beneficiary;` is set before the token loop even runs (line 684). So the order's escrow accounting is permanently decremented and the order is marked settled/filled regardless of whether the beneficiary actually received the tokens.

Contrast this with the inbound path in the same file, which correctly uses `SafeERC20.safeTransferFrom`: [2](#0-1) 
and with the canonical EVM implementation (`IntentsBase.sol`), whose `_withdraw` uses `IERC20(token).safeTransfer(beneficiary, amount)`: [3](#0-2) 

The Tron variant's `SweepDust` handler has the identical unchecked-call pattern: [4](#0-3) 

### Impact Explanation
This is a direct loss/lock of bridged user funds: on a `RedeemEscrow`, `RefundEscrow`, or same-chain cancel/withdraw with a non-standard ERC20 that returns `false` (instead of reverting) on transfer failure (e.g., insufficient allowance edge cases in proxy/upgradeable tokens, blacklist-style tokens that "succeed" the call but return false, or any token following the pre-EIP20 "return false on failure" pattern), the contract will mark the order `_filled` and zero out `_orders[commitment][token]` without the beneficiary ever receiving the asset. The tokens remain trapped in the `IntentGatewayV2` contract with no accounting path left to reclaim them (the commitment is already consumed, so no repeat `RedeemEscrow`/`RefundEscrow`/cancel call can succeed — every guard checks `_orders[commitment][token] == 0` or `_filled[commitment] != address(0)`, both of which are now true). This satisfies the bounty's "loss of funds" and "false settlement" criteria and is reachable purely through the normal, unprivileged cross-chain settlement flow (an attacker doesn't need to be a relayer/prover — they only need the input or output token used in an order to exhibit this common non-reverting-failure behavior, or for the deployer/governance to list such a token for a destination).

### Likelihood Explanation
Likelihood is moderate: it requires a token that returns `false` rather than reverting on transfer failure to be used as an order's input/output asset. This is a well-documented class of tokens in production (a subset of older/legacy ERC20/TRC20 deployments), and IntentGatewayV2 does not appear to enforce an allowlist restricting `order.inputs`/`order.output.assets` to "safe" tokens in the reviewed code. Because the bug sits in the settlement hot path (every fill and refund), a single instance of a non-conforming token used in any order silently burns that order's escrow.

### Recommendation
Replace the raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `withdraw()` and the `SweepDust` branch of `onAccept` with OpenZeppelin's `SafeERC20.safeTransfer`, exactly as already done for the inbound `safeTransferFrom` calls in `placeOrder` and as done in the canonical `IntentsBase.sol`. This ensures both call success and the ERC20 return value (or absence thereof, per EIP-20) are validated before the commitment's escrow state is finalized.

### Proof of Concept
1. Deploy a mock ERC20 whose `transfer(address,uint256)` returns `false` when `amount > balanceOf(from)` or under some attacker-controlled condition, without reverting (a common pattern for tokens implementing pre-2018 ERC20 semantics).
2. Use this token as `order.inputs[0].token` in `placeOrder`; the escrow is correctly funded via `safeTransferFrom`.
3. Trigger the fill/cancel flow so `onAccept`/`onGetResponse` calls `withdraw(body, ...)` with this token and an `amount` that causes the mock's `transfer` to return `false` while not reverting.
4. Observe: `withdraw()` treats `success == true`, decrements `_orders[commitment][token]` to 0, sets `_filled[commitment] = beneficiary`, and emits `EscrowReleased`/`EscrowRefunded` — yet `beneficiary`'s token balance never increased. The tokens are now unrecoverable through any code path in the contract, since every re-entry guard is now satisfied (order "already filled"/"unknown order").

Note: I could not fully audit whether an allowlist of tokens exists elsewhere in deployment/governance tooling outside this contract file that would prevent non-conforming tokens from ever being escrowed; if such an allowlist exists and is enforced off-chain only (not on-chain in this contract), the on-chain guarantee is still missing and the analog holds.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L399-399)
```text
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
```

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L405-409)
```text
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
