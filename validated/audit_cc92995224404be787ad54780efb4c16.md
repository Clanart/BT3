## Analysis

The external report's core defect is: **token-transfer return values are not validated, so a call that "succeeds" at the EVM level (does not revert) but signals failure via its encoded return data is silently treated as a real transfer.** In the original Notional bug this manifested as an unwanted revert; the durable, more dangerous form of the same defect is the inverse — treating a `false`-returning (non-reverting) token call as success, which causes state to advance as if funds moved when they didn't.

Hyperbridge's Tron variant of the Intent Gateway reproduces exactly this inverse defect.

### Where it lives

`evm/tron/contracts/apps/IntentGatewayV2.sol` deliberately avoids `SafeERC20` for escrow payout paths and instead does raw low-level calls, checking only whether the call reverted — never decoding/validating the returned boolean: [1](#0-0) 

Specifically in `withdraw()`:
- `(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount)); if (!success) revert TransferFailed();` — then unconditionally does `_orders[body.commitment][token] -= amount;` and, for fees, `feeToken.call(...)` with the same unchecked-return-data pattern, then finalizes `_filled[body.commitment] = beneficiary;`.

The `SweepDust` handler in `onAccept` has the identical pattern: [2](#0-1) 

By contrast, the canonical EVM contracts consistently use `SafeERC20`, which decodes and asserts the boolean return value in addition to checking for reverts: [3](#0-2) [4](#0-3) 

Note the Tron file even imports `SafeERC20` and declares `using SafeERC20 for IERC20;` at the top, but the payout/withdraw/sweep code paths deliberately bypass it in favor of raw `.call()` with a `success`-only check: [5](#0-4) 

### Title
Unchecked ERC20 return-data on escrow payout lets a non-reverting-failure token permanently burn escrow accounting without delivering funds - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`IntentGatewayV2.withdraw()` (Tron variant) and its `SweepDust` handler release escrowed input tokens and transaction fees via a raw low-level `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))`, checking only that the call did not revert (`success`). Neither function inspects the ABI-encoded return value. Any ERC20-like token on Tron that returns `false` on a failed `transfer` (rather than reverting) — a legitimate, spec-compliant ERC20 pattern per EIP-20 — will pass this check even though no tokens were moved. The function nonetheless decrements the escrow accounting (`_orders[commitment][token] -= amount`) and finalizes the order (`_filled[commitment] = beneficiary`), permanently destroying the record that funds are still owed, while the tokens remain trapped in the contract.

### Finding Description
`withdraw()` is the single choke point used both for `RedeemEscrow`/`RefundEscrow` (destination-triggered, `onAccept`) and for same-chain cancellation refunds (`cancelOrder`). For every token in the withdrawal:

```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
_orders[body.commitment][token] -= amount;
```

`success` only reflects whether the callee reverted; it says nothing about the decoded boolean the ERC20 standard requires `transfer` to return. A token that implements `transfer` to return `false` on failure (e.g., insufficient allowance-independent internal restriction, blacklist, paused state, or any bespoke TRC20 token deployed on Tron that follows this common non-reverting-failure pattern) will make the low-level call return `success = true` with `returndata = abi.encode(false)`. The contract treats this identically to a real transfer: it deducts the escrowed amount and, in the finalize branch, marks `_filled[commitment] = beneficiary` and clears the fee escrow — an irreversible state transition. There is no retry path once `_filled` is set and `_orders[...]` is zeroed, so the beneficiary permanently loses the escrowed funds even though the tokens are still sitting in the contract's balance.

The same unchecked pattern appears in the `SweepDust` administrative handler, which is reached via `onAccept` from a governance-authenticated Hyperbridge message but suffers the identical silent-failure/loss-of-accounting problem for dust redistribution.

This is the same broken invariant as H-07 (return-value handling for non-standard ERC20 tokens breaks fund movement), just manifesting as false-success rather than spurious-revert: **the code never actually verifies the token moved before mutating irreversible escrow/settlement state.**

### Impact Explanation
Funds can be permanently locked/lost: escrow debited and order finalized while the beneficiary receives nothing, with no compensating mechanism because `_filled` is a one-time-set mapping and `_orders[commitment][token]` has already been decremented to (or below) zero. This satisfies the bounty's "stealing or loss of funds" and "false [settlement] state acceptance" criteria — the redemption records settlement as complete based on unverified execution.

### Likelihood Explanation
Reachable by any unprivileged actor who places or fills an order using a token that returns `false` instead of reverting on transfer failure (a legal, spec-compliant ERC20/TRC20 behavior, not a "malicious peer" or admin-only condition). Because the Tron deployment explicitly targets the Tron TRC20 ecosystem where such non-reverting tokens are common, the attack surface is realistic and directly tied to production custody logic (`_orders`, `_filled`), not to relayer, prover, or governance trust assumptions.

### Recommendation
Replace every raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` / `IERC20.transferFrom` low-level pattern in `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `withdraw()` and `SweepDust` handling with `SafeERC20.safeTransfer`/`safeTransferFrom` (already imported and aliased via `using SafeERC20 for IERC20;` at the top of the file), which decodes and asserts the boolean return value in addition to checking for reverts, consistent with the rest of the codebase (e.g. `IntentsBase.sol`, main-chain `IntentGatewayV2.sol`).

### Proof of Concept
1. Deploy (or use) a TRC20-style token `T` whose `transfer(to, amount)` returns `false` (instead of reverting) when `to` is blacklisted or the internal transfer restriction fails, while still returning valid ABI-encoded data.
2. User places an order on the Tron `IntentGatewayV2` escrowing `amount` of `T`; solver fills it, or the user cancels and triggers `withdraw()` with `beneficiary` set to an address `T` will reject.
3. `withdraw()` executes `token.call(...)`; the call does not revert (`success == true`) but the encoded return data is `false` — undetected because the code never decodes it.
4. `_orders[commitment][token] -= amount` succeeds, and `_filled[commitment] = beneficiary` is set, finalizing the order.
5. Token `T` balance of `IntentGatewayV2` is unchanged (transfer silently failed); beneficiary never receives funds, and the escrow record no longer reflects any owed balance — funds are permanently stranded in the contract with no recovery path.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L38-39)
```text
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L55-56)
```text
contract IntentGatewayV2 is HyperApp, EIP712 {
    using SafeERC20 for IERC20;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-674)
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L404-410)
```text
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```
