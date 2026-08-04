Confirmed: this `withdraw()` function on Tron's `IntentGatewayV2.sol` uses a raw low-level `.call()` with `IERC20.transfer.selector`, checking only that the *call itself* did not revert (`success`), but never decoding/validating the boolean return value that ERC20's `transfer()` is supposed to return.

### Title
Unchecked ERC20 `transfer()` return value in escrow `withdraw()` permanently locks funds while marking escrow as redeemed - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
### Finding Description
`withdraw()` releases escrowed order funds to the beneficiary by making a raw low-level call to the token's `transfer` selector and only checking that the low-level call did not revert: [1](#0-0) 
For any ERC20-like token that returns `false` on failure instead of reverting (a legal, non-standard-but-common pattern, e.g. some legacy/no-revert tokens deployed on Tron/TRC20), the low-level `call` still returns `success == true` (the call executed without reverting), even though no tokens were actually moved. The code treats this as a successful transfer and proceeds to decrement escrow accounting unconditionally: [2](#0-1) 
and emits `EscrowReleased`/`EscrowRefunded` as if funds were delivered. The identical pattern also appears in the `SweepDust` handling of `onAccept` and in the transaction-fee redemption branch of the same function: [3](#0-2) [4](#0-3) 

This is a direct structural analog of the reported bug class: a value-transfer primitive is assumed to reliably deliver funds based on a shallow success signal, but the actual delivery can silently fail, and the contract's bookkeeping (escrow debit, `_filled` mapping, emitted events) is updated as though the transfer succeeded regardless. In the original report the failure mode was `transfer()`'s 2300-gas stipend; here it is a non-compliant/no-revert ERC20 `transfer()` returning `false`. In both cases, the guard the code relies on (`!sent`/`!success` from the top-level call) does not detect the actual failure, and downstream state is committed as if the funds moved.

Note that the sibling EVM implementation (`evm/src/apps/IntentGatewayV2.sol` and `evm/src/apps/intentsv2/IntentsBase.sol`) uses OpenZeppelin's `SafeERC20.safeTransfer`, which does decode and enforce the boolean return value, so this weaker pattern is isolated to the Tron variant of `IntentGatewayV2.sol`.

### Impact Explanation
Escrowed input tokens (`_orders[commitment][token]`) are decremented and the order is marked as `_filled`/refunded even though the beneficiary never received the underlying tokens for any token whose `transfer()` returns `false` on failure. This produces a permanent loss of the escrowed principal: the tokens remain stranded in the `IntentGatewayV2` contract, the on-chain accounting says the order was already redeemed, and there is no remaining code path to re-attempt or reclaim the amount for that commitment (the escrow slot is already zeroed and `UnknownOrder()`/replay guards will now reject any retry). This matches the required impact class of loss of funds / false settlement acceptance without needing a malicious relayer, prover, or admin — an unprivileged caller only needs the order or dust-sweep to involve a token that is TRC20-compatible but non-reverting-on-failure.

### Likelihood Explanation
Likelihood depends on which tokens are configured as order inputs/outputs on the Tron deployment; TRC20 tokens (Tron's ERC20 analog) do not universally guarantee revert-on-failure semantics the way audited OpenZeppelin ERC20 does, and several widely used tokens historically return `false` rather than revert. Given that Hyperbridge's `IntentGatewayV2` on other chains explicitly uses `SafeERC20` while the Tron variant does not, this looks like an unintentional regression/omission specific to the Tron port rather than a deliberate design choice, making the vulnerable condition realistically reachable whenever a non-standard token is whitelisted for orders.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern throughout `evm/tron/contracts/apps/IntentGatewayV2.sol` (`withdraw`, the `SweepDust` branch of `onAccept`, and the transaction-fee redemption branch) with OpenZeppelin's `SafeERC20.safeTransfer`, matching the pattern already used in `evm/src/apps/IntentGatewayV2.sol` and `evm/src/apps/intentsv2/IntentsBase.sol`. This decodes and validates the ERC20 return data (when present) in addition to checking the call did not revert, eliminating the silent-failure window before escrow state is finalized.

### Proof of Concept
1. Configure an `IntentGatewayV2` order (or `SweepDust` request) whose input/output token is a TRC20 token that implements `transfer()` to return `false` (no revert) when, e.g., the caller's balance check fails or a blacklist/pause condition is hit, instead of reverting.
2. Have a solver fill the order (or trigger the redeem/refund/`SweepDust` path) such that `withdraw()` is invoked for that token with an amount the token's `transfer()` will reject via `false` return rather than revert.
3. Observe: `token.call(...)` returns `success == true` because the call executed without reverting; the `if (!success) revert TransferFailed();` guard does not fire.
4. `_orders[body.commitment][token] -= amount;` executes, permanently zeroing/reducing the escrow record and emitting `EscrowReleased`, while the beneficiary's TRC20 balance never increased — the tokens remain stuck in the `IntentGatewayV2` contract with no code path left to redeem them for that commitment.

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
