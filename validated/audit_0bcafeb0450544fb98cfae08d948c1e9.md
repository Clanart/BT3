## Analysis

The external report's core broken invariant: the vault's internal accounting can mark a claim as satisfied/settled while the actual token balance is never delivered to the rightful party, because there is no hard link between "record says paid" and "tokens actually moved." I looked for a Hyperbridge analog where escrow-release/settlement accounting is decoupled from the actual on-chain transfer success. The `IntentGatewayV2` escrow-settlement path on the Tron EVM fork is the strongest match: it uses raw low-level `.call()` for ERC-20 transfers and only checks that the *call itself* didn't revert, never inspecting the ERC-20 boolean return value.

### Title
Silent ERC-20 Transfer Failure Marks Intent Orders as Settled Without Delivering Funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` (the Tron-chain fork of the Intent Gateway) settles escrowed inputs, refunds, and fee payouts using a raw low-level `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only reverts if the call itself reverts. It never decodes/checks the ERC-20 `bool` return value.

### Finding Description
On a successful fill, cancel-from-source, or cancel-from-dest settlement message, `onAccept`/`onGetResponse` call `withdraw(body, isRefund)`: [1](#0-0) 

The transfer is performed as:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```
and the same pattern is repeated for the fee-token payout at the end of `withdraw()`. `success` here only reflects whether the low-level call *reverted*; many ERC-20/TRC20 tokens (including some legacy/blacklist-capable tokens common on Tron and several mainstream ERC-20s) return `false` from `transfer()` on failure (insufficient balance, blacklisted recipient, paused state, etc.) **without reverting**. In that case `success == true` even though zero tokens moved.

Immediately after this unchecked call, the function unconditionally decrements the escrow ledger and mutates state as if the payout succeeded:
```solidity
_orders[body.commitment][token] -= amount;
```
and (for fully-filled/finalized bodies) marks `_filled[body.commitment] = beneficiary` and emits `EscrowReleased`/`EscrowRefunded`.

Contrast this with the primary EVM `IntentsBase.sol` implementation, which correctly uses OpenZeppelin's `SafeERC20.safeTransfer`, which decodes and enforces the boolean return value: [2](#0-1) 

The Tron variant's `withdraw()` diverges from that safe pattern: [3](#0-2) 

### Impact Explanation
This is a false-state-acceptance / fund-loss bug: the gateway's internal commitment/order state (`_orders`, `_filled`) declares the escrow "settled" (filled or refunded) and emits the corresponding event, even though the token transfer silently failed and the beneficiary received nothing. Because `_orders[commitment][token]` is decremented and `_filled[commitment]` is set regardless of transfer outcome, there is no retry path — the tokens remain stuck in the contract, unattributed to any beneficiary, and the user/solver who was supposed to be paid loses the funds outright. This directly matches the bounty's "stealing or loss of funds" and "false proof/state acceptance" categories, since a cross-chain settlement message that should have paid out is accepted and finalized without the promised payout occurring.

### Likelihood Explanation
This path is reachable by unprivileged actors through the ordinary intent lifecycle: any solver filling a cross-chain order, any user/relayer triggering a source- or destination-side cancellation, or any legitimate settlement message delivered via `onAccept`/`onGetResponse` will invoke `withdraw()`. No malicious relayer, prover, or governance actor is required — it only requires a token whose `transfer()` can return `false` without reverting, which is a documented, common ERC-20 implementation pattern (and one of the reasons `SafeERC20` exists and is used elsewhere in this very codebase).

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `IntentGatewayV2.withdraw()` (and the fee-token payout in the same function, and the dust-sweep path) with OpenZeppelin's `SafeERC20.safeTransfer`, mirroring `IntentsBase.sol`. If `SafeERC20` cannot be used verbatim on Tron's TRC20 dialect, at minimum decode and require the returned boolean (`success && (data.length == 0 || abi.decode(data, (bool)))`) before mutating `_orders`/`_filled` or emitting settlement events, and revert (or fall back to holding escrow) on failure so the ledger never diverges from actual token custody.

### Proof of Concept
1. Deploy/select an ERC-20/TRC20 token whose `transfer()` returns `false` (rather than reverting) under some condition — e.g., a token with a `paused` flag, a blacklist, or an internal balance check that returns `false` instead of reverting on insufficient balance.
2. Place a cross-chain intent order using this token as an output/input asset via `IntentGatewayV2` on the Tron deployment.
3. Trigger settlement so that `token.transfer(beneficiary, amount)` internally hits the failing condition and returns `false` while the outer call succeeds (no revert bubbles up).
4. Observe that `withdraw()` proceeds: `_orders[commitment][token] -= amount` executes, `_filled[commitment]` is set, and `EscrowReleased`/`EscrowRefunded` is emitted — yet `beneficiary`'s token balance never increased. The escrowed tokens remain trapped in the gateway with no code path to reclaim them, since the commitment is now marked settled.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-721)
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
        }
    }

    /**
     * @notice Withdraws the escrowed tokens for a request body.
     * @dev This function is marked as internal.
     * @param body The request body containing commitment, tokens, and beneficiary.
     * @param isRefund Whether this is a refund (true) or a successful fill (false).
     */
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
