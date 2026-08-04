Based on my research, I found a concrete local analog. While Hyperbridge's core `EvmHost.sol` and `pallet-bandwidth`/`host-executive` pallets consistently emit events for sensitive state changes (e.g., `HostParamsUpdated`, `HostWithdrawal`, `BandwidthCredited`, `ForceCredited`), the underlying bug-class pointer — "sensitive action executed without verifying/recording the actual result before committing state and notifying observers" — reproduces concretely in the Tron variant of the Intent Gateway's settlement path, where the contract trusts a low-level call's outer success flag instead of the token's actual return value before marking an order permanently settled.

### Title
Unchecked ERC-20 return value in intent settlement lets `withdraw()` mark orders filled/refunded while the token transfer silently fails - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.withdraw()` on the Tron variant of the Intent Gateway moves escrowed funds using a raw low-level `.call` to `IERC20.transfer`, checking only that the call itself did not revert (`success`), not the boolean return value that `transfer` is supposed to yield. Any token that returns `false` on failure instead of reverting will cause the gateway to treat a failed transfer as successful, permanently marking the order as filled/refunded and emitting the corresponding settlement event, with no recovery path.

### Finding Description
In `withdraw()`: [1](#0-0) 

the function immediately sets `_filled[body.commitment] = beneficiary` before performing any transfers, then for each token does:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```
This only checks that the external call didn't revert — it never decodes/verifies the ABI-encoded boolean return value. Standard-but-non-strict ERC-20 tokens (including some tokens deployed under Tron's TRC-20 conventions, which frequently diverge from strict Solidity ERC-20 semantics) can return `false` on failure without reverting. In that case `success == true`, the branch is skipped, and the loop proceeds to decrement `_orders[body.commitment][token] -= amount` and finally emits `EscrowReleased`/`EscrowRefunded` — even though no tokens actually moved.

The same unchecked pattern is used for the fee redemption block just below it and in the `SweepDust` handler in `onAccept`: [2](#0-1) [3](#0-2) 

Because `_filled[body.commitment]` is set unconditionally at the top of `withdraw()`, and both `RedeemEscrow`/`RefundEscrow` inbound paths route through this single function with one-time-receipt-style state (`onAccept` in `authenticate()` and `onGetResponse`'s `Filled()` check), there is no mechanism to retry or recover once the commitment is marked filled with a token that returned `false`. This directly breaks the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" invariant: the commitment is consumed, but the beneficiary receives nothing.

### Impact Explanation
This is a fund-loss/fund-lock bug in the intent settlement path: escrowed source-chain (or cross-chain redemption) assets can be permanently stranded in the `IntentGatewayV2` contract while the protocol's own bookkeeping (`_orders`, `_filled`) and public events (`EscrowReleased`/`EscrowRefunded`) report a successful settlement. Any relayer, indexer, or user relying on those events/state to confirm delivery would be misled, and the affected user has no path to reclaim the escrowed funds since the commitment is already marked filled.

### Likelihood Explanation
The trigger does not require a malicious relayer, prover, or admin — it only requires that one of the tokens used in an order's `inputs`/`outputs` set is a real-world ERC-20/TRC-20 token that returns `false` on a failed `transfer` instead of reverting (a well-documented deviation present in numerous deployed tokens, and especially plausible on Tron's TRC-20 ecosystem, which this contract file specifically targets). Any user or filler placing an order with such a token is exposed; the path is reachable via the normal `RedeemEscrow`/`RefundEscrow` message flow that Hyperbridge delivers after ordinary cross-chain settlement/cancellation, i.e., a standard production code path, not a testnet-only or malicious-operator scenario.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()`, the fee-redemption block, and `SweepDust` with a safe-transfer helper (e.g., OpenZeppelin's `SafeERC20.safeTransfer`, as already used in the main EVM `IntentGatewayV2`/`IntentsBase` contracts) that decodes and asserts any returned boolean, reverting on `false` in addition to reverting on outright call failure. Additionally, consider not writing `_filled[body.commitment]` until after all transfers are confirmed to succeed, so a failed transfer can be retried rather than permanently locking the commitment.

### Proof of Concept
1. Deploy (or use) an ERC-20/TRC-20-style token `T` whose `transfer()` returns `false` on failure conditions (e.g., transfer to a blacklisted address, insufficient allowance-style internal check) rather than reverting — this is standard behavior for several non-strict tokens.
2. Place a cross-chain order on `IntentGatewayV2` (Tron variant) using `T` as one of the `inputs`/`outputs` tokens; the tokens get escrowed in the gateway.
3. Trigger the destination-to-source `RefundEscrow` or `RedeemEscrow` flow so `onAccept` → `withdraw()` executes, with `T.transfer(beneficiary, amount)` returning `false` under the fault condition.
4. Observe: `success == true` (the call itself succeeds), so `if (!success) revert TransferFailed();` does not trigger; `_orders[body.commitment][token] -= amount` executes; `_filled[body.commitment] = beneficiary` was already set; the function emits `EscrowRefunded`/`EscrowReleased`.
5. Confirm `T.balanceOf(beneficiary)` did not increase, while `_filled[commitment]` is now permanently set — the escrowed `T` tokens remain stuck in the `IntentGatewayV2` contract with no code path to retry or reclaim them.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-672)
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
