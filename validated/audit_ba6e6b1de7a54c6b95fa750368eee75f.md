## Title
Unchecked ERC20 return value in escrow withdrawal lets non-standard tokens silently fail while escrow is marked redeemed — permanent fund loss for beneficiary - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.sol` (Tron variant) settles escrowed intent funds using a raw low-level `.call()` to the ERC20 `transfer` selector and only checks that the *call itself* did not revert (`success`), never decoding/verifying the boolean return value the ERC20 standard defines for `transfer()`. This is the exact bug class from the external report ("use `safeTransferFrom`/`safeTransfer` from SafeERC20 … result of functions is not checked"), reproduced locally in the escrow redemption, refund, fee-payout, and dust-sweep paths of the intents settlement flow, instead of the `SafeERC20.safeTransfer` pattern correctly used in the sibling EVM contract `evm/src/apps/intentsv2/IntentsBase.sol`.

### Finding Description
In `withdraw()`, `onAccept()` (SweepDust branch), and the fee-redemption block, token payout uses: [1](#0-0) 

```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```
followed unconditionally by:
```solidity
_orders[body.commitment][token] -= amount;
```

`success` here only reflects whether the external call reverted — it does not inspect the `returndata`. Some ERC20 tokens (e.g. legacy tokens that return `false` on failure instead of reverting, tokens with transfer hooks/allow-lists, blacklist/pausable tokens) can return `abi.encode(false)` while the call itself completes without reverting. In that case `success == true`, the `if (!success) revert TransferFailed()` guard never fires, yet the beneficiary receives nothing.

The same unguarded pattern appears three times in this file:
- `withdraw()` — escrow token payout: [2](#0-1) 
- `withdraw()` — transaction fee payout: [3](#0-2) 
- `onAccept()` — `SweepDust` branch: [4](#0-3) 

Immediately after the unchecked "success", the contract mutates critical state as if the transfer definitely happened:
```solidity
_orders[body.commitment][token] -= amount;
...
_filled[body.commitment] = beneficiary;
...
emit EscrowReleased({commitment: body.commitment});
``` [5](#0-4) 

This contrasts with the correct pattern used elsewhere in the codebase for the same escrow-release logic, `evm/src/apps/intentsv2/IntentsBase.sol`, which uses `SafeERC20.safeTransfer`, reverting on both a failed call and a token that returns `false`: [6](#0-5) 

Also note `withdraw()`'s only escrow guard is `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` — it does not verify `amount <= escrowed` before subtracting, so once the state marks `_filled`/decrements the escrow on a "successful-looking" but actually-failed transfer, there is no path back to retry or recover the funds: `onAccept` is `onlyHost`-gated and single-shot per commitment via `_filled`/`_orders` bookkeeping.

### Impact Explanation
This falls squarely under "stealing or loss of funds" and "false … state acceptance" in the bounty scope: the contract's internal accounting (`_orders`, `_filled`) is updated to reflect a completed release/refund even though the beneficiary never received the tokens. Because `_orders[commitment][token]` is decremented and `_filled[commitment]` set, the funds cannot be re-claimed by any other code path (they become permanently stranded in the gateway contract, effectively lost to the intended beneficiary), while the protocol simultaneously emits `EscrowReleased`/`EscrowRefunded`/`DustSwept` asserting success. This is fund loss caused entirely by unprivileged, ordinary settlement flow (a solver filling/redeeming an order, or hyperbridge relaying a normal `RedeemEscrow`/`RefundEscrow`/`SweepDust` request) whenever the escrowed/output token is one of the (still commonly used) non-reverting-on-failure ERC20 implementations — no malicious relayer, prover, or admin action is required.

### Likelihood Explanation
Likelihood is directly tied to which ERC20 tokens are supported by the Tron deployment of the IntentGateway. Given IntentGatewayV2 is generic and accepts arbitrary `token` addresses supplied in orders (`order.inputs[i].token` / `order.output.assets[i].token`), and Tron/TRC20 tokens historically have more variance in ERC20 compliance than mainnet EVM tokens (several well-known TRC20 tokens do not strictly follow the boolean-return convention), the probability of encountering an incompatible token in production is non-trivial, and the trigger condition (a `transfer` call that returns `false` without reverting) requires no attacker collusion — it can also be triggered deliberately by a solver/beneficiary who is aware a particular token behaves this way, to force the contract into believing settlement completed.

### Recommendation
Replace every raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` + `success`-only check in `evm/tron/contracts/apps/IntentGatewayV2.sol` (`withdraw()` escrow payout, `withdraw()` fee payout, `onAccept()` `SweepDust` branch) with OpenZeppelin's `SafeERC20.safeTransfer`, matching the already-correct pattern in `evm/src/apps/intentsv2/IntentsBase.sol`. This decodes and enforces the ERC20 return value (when present) in addition to requiring the call itself to succeed, and reverts the whole transaction — including the `_orders`/`_filled` state mutations — if the token transfer did not truly succeed.

### Proof of Concept
1. Deploy (or use an existing) TRC20/ERC20 token whose `transfer()` implementation returns `false` on failure instead of reverting (e.g., a token with a transfer allow-list/blacklist check that returns `false` for a disallowed recipient, rather than reverting).
2. Create and fill an intent order using this token as an input/output asset via `IntentGatewayV2`, so that `_orders[commitment][token]` is escrowed.
3. Cause the beneficiary address to become disallowed by the token (e.g., blacklisted) after escrow but before settlement — or simply use a token where the beneficiary is not on an allow-list.
4. Trigger `onAccept` with `RequestKind.RedeemEscrow` (or `RefundEscrow`), which calls `withdraw()`:
   - `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` executes; the token's internal logic returns `false` but the low-level call does not revert, so `success == true`.
   - `if (!success) revert TransferFailed();` does not fire.
   - `_orders[body.commitment][token] -= amount;` proceeds, `_filled[body.commitment] = beneficiary;` is set, and `EscrowReleased` is emitted.
5. Verify on-chain: the beneficiary's token balance is unchanged (transfer silently failed), but `_orders[commitment][token]` is now zero/decremented and `_filled[commitment]` is set — the tokens remain locked in the `IntentGatewayV2` contract with no remaining code path to redeem them to the intended beneficiary.

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
