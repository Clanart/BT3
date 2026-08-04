### Title
Non-standard ERC20 tokens (no-bool / silent-false) permanently lock funds and can wrongly finalize withdrawals in `IntentGatewayV2` (Tron variant) - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
`evm/tron/contracts/apps/IntentGatewayV2.sol` settles escrowed intent funds using a raw low-level `.call` to `IERC20.transfer` and only checks that the *call itself* did not revert (`success`), never decoding/validating the returned `bool` payload. This is the exact bug class from the external report (non-standard ERC20 tokens that return `false` instead of reverting, or return no data at all) applied to Hyperbridge's intent-settlement custody path rather than a vesting contract.

### Finding Description
In `withdraw()` (escrow release/refund) and the `SweepDust` request handler, token payouts use:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
``` [1](#0-0) 
and identically in the dust-sweep governance handler: [2](#0-1) 

`success` here only reflects whether the external call reverted, not whether the token's own `transfer` logic actually succeeded. For ERC20 implementations that:
1. Return `false` on failure instead of reverting (a common non-standard pattern the report explicitly calls out), the call succeeds (`success == true`) even though no tokens moved.
2. Return no data at all (e.g., USDT-style), the ABI decode of the return value is skipped entirely here (only `success` is checked, so this particular variant is actually tolerated) — but variant (1) is not caught.

Because the check never inspects the decoded boolean, a token behaving like variant (1) causes `withdraw()` to proceed as if the transfer succeeded: it decrements `_orders[body.commitment][token]`, marks `_filled[body.commitment] = beneficiary`, and emits `EscrowReleased`/`EscrowRefunded` — permanently finalizing the order and destroying the accounting record for escrowed funds that were never actually delivered to the beneficiary. The tokens remain locked in the `IntentGatewayV2` contract with no `_orders` entry left to redeem them, an unrecoverable fund loss for the intended recipient (solver, filler, or refunded user). This directly parallels `TenetVesting::rescueFunds`/`VestRewardReceiver::sendTokens` in the source report, but manifests in Hyperbridge's cross-chain escrow settlement path instead.

Note: The corresponding non-Tron mainline contract at `evm/src/apps/intentsv2/IntentsBase.sol` uses OpenZeppelin `SafeERC20.safeTransfer` for the equivalent `_withdraw()` logic [3](#0-2) , so this is a regression/inconsistency specific to the Tron deployment target of `IntentGatewayV2.sol`.

### Impact Explanation
This qualifies as fund loss under the bounty scope: escrowed bridge assets can be marked as released/refunded (one-time state finalized) while the beneficiary receives nothing, for any ERC20 token integrated into `IntentGatewayV2` on Tron that returns `false` rather than reverting on failed transfer (e.g., insufficient balance edge-cases, blacklist/pausable tokens, or fee/rebase tokens with unusual failure semantics). Once `_orders[...] -= amount` and `_filled[...]` are set, there is no retry path — the tokens are stuck in the contract with no accounting key to recover them.

### Likelihood Explanation
Likelihood is moderate to low depending on which ERC20 tokens are whitelisted/used with the Tron `IntentGatewayV2` deployment; it requires a token whose `transfer` can return `false` without reverting under some condition an unprivileged party can trigger (e.g., temporary blacklist, paused state, or insufficient balance in weird proxy implementations). No malicious relayer, prover, or admin is needed — a normal user interacting with such a token triggers the broken path.

### Recommendation
Replace the raw `.call` + `success`-only check with OpenZeppelin's `SafeERC20.safeTransfer` (already imported in the file) in both `withdraw()` and the `SweepDust` handler in `evm/tron/contracts/apps/IntentGatewayV2.sol`, matching the pattern already used in `evm/src/apps/intentsv2/IntentsBase.sol`.

### Proof of Concept
1. Configure `IntentGatewayV2` (Tron) to accept a token `T` whose `transfer(to, amount)` returns `false` (no revert) when, e.g., `to` is blacklisted or a transient condition triggers, while otherwise behaving like a normal ERC20.
2. A user escrows `T` into an order; a filler/solver later triggers `withdraw()` (via `onGetResponse`/refund or fill-completion path) with a `beneficiary` under the failure condition.
3. `token.call(...)` returns `success = true` (call didn't revert) but the token's internal `transfer` logic returns `false` and does not move funds.
4. `withdraw()` proceeds: `_orders[commitment][token] -= amount` and `_filled[commitment] = beneficiary` are set, `EscrowReleased`/`EscrowRefunded` is emitted — the order is finalized while `T` remains stuck in the `IntentGatewayV2` contract with no accounting path left to reclaim it. [4](#0-3)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-667)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-409)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
