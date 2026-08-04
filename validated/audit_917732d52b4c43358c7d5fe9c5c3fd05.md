## Analysis

The Tron-specific fork of the IntentGateway diverges from the audited EVM implementation in exactly the way the RealityCards report warns against: it replaces OpenZeppelin's `SafeERC20.safeTransfer` (used everywhere else in the codebase, e.g. `IntentsBase._withdraw` at [1](#0-0) ) with a raw low-level `.call` that only checks whether the call reverted, never whether the ERC20 token itself signaled success via its boolean return value.

### Title
Unchecked ERC20 `transfer` return value in Tron IntentGateway escrow withdrawal and dust sweep permanently desyncs escrow accounting from actual token balance - (`evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` and the `SweepDust` branch of `onAccept()` in the Tron `IntentGatewayV2` contract move escrowed ERC20 tokens using `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only revert if the low-level call itself reverts (`!success`). They never inspect the ABI-decoded boolean return value of `transfer`. Any ERC20 that follows the "return false, don't revert" convention on failure (well documented as a common non-standard behavior — the exact bug class from the H-01 report) will cause `success == true` while no tokens actually move, yet the contract unconditionally decrements its internal escrow ledger (`_orders[body.commitment][token] -= amount`) and marks the order `_filled`/finalized as if the transfer succeeded.

### Finding Description
In `withdraw()`: [2](#0-1) 

and in the `SweepDust` handling branch of `onAccept()`: [3](#0-2) 

both use the pattern:
```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```

This only guards against the call reverting or the target having no code executing an EVM-level revert — it does not decode/verify the returned `bool`. Tokens that return `false` on failed transfer (rather than reverting) pass this check unconditionally. The escrow accounting (`_orders[body.commitment][token] -= amount;`) and order-finalization (`_filled[body.commitment] = beneficiary;`) proceed regardless of whether the beneficiary actually received the funds.

Contrast this with the canonical, audited path in `IntentsBase._withdraw` (used by the main EVM and reference implementations), which uses `IERC20(token).safeTransfer(beneficiary, amount);` — OpenZeppelin's `SafeERC20` decodes and asserts the boolean return value (or requires no return data), reverting the whole transaction if the token signals failure: [4](#0-3) 

The Tron contract even imports `SafeERC20` and uses `.safeTransferFrom` on the deposit side (`placeOrder`), but abandons it specifically on the withdrawal/sweep side where it matters most for correctness of accounting: [5](#0-4) 

### Impact Explanation
This is a direct loss-of-funds / permanent-lock analog to H-01. For any listed input/output token that does not strictly revert-on-failure (a known real-world ERC20 non-conformance class, and one the project's own docs and other contracts explicitly guard against via `SafeERC20`):
- A beneficiary's `withdraw()` (order fill settlement or cancellation refund) can silently fail to deliver tokens while the escrow ledger `_orders[commitment][token]` is decremented and the order is marked `_filled` — the user's escrowed funds are effectively lost (the tokens remain stuck in the contract, unreachable since the accounting no longer reflects them as owed to anyone, and no compensating credit is issued).
- The same applies to `SweepDust`, where protocol dust could be marked swept without actually reaching the beneficiary, with no revert and no way to retry since there is no per-token receipt tracking to detect the discrepancy.

This directly matches the required impact categories: "stealing or loss of funds" and "false proof/state acceptance" in the sense that escrow state is finalized without the corresponding value transfer actually occurring.

### Likelihood Explanation
No malicious peer, relayer, prover, or governance actor is required — any unprivileged party can trigger `onAccept`-driven withdrawal paths simply by being the legitimate beneficiary of a normal cross-chain order settlement/cancellation once such a token is configured as an order input/output on the Tron deployment. The vulnerability is purely a function of which ERC20 token is used for an order; it requires no compromise of any protocol actor, matching the "no malicious peer/relayer/prover assumption" requirement.

### Recommendation
Replace the raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` and the `SweepDust` branch of `onAccept()` with `IERC20(token).safeTransfer(beneficiary, amount)` (already imported and used elsewhere in the same file), consistent with the reference `IntentsBase._withdraw` implementation. This ensures the boolean return value (or absence of return data per EIP-20) is properly validated before the escrow ledger is decremented and the order finalized.

### Proof of Concept
1. Deploy (or configure) an ERC20 token on Tron whose `transfer` function returns `false` on failure instead of reverting (e.g., insufficient balance in the gateway due to a prior accounting bug, a paused/blacklist-style token, or any non-strict ERC20).
2. Place a cross-chain order using this token as an input on the source chain via `placeOrder`, escrowing funds into `_orders[commitment][token]`.
3. On settlement, the destination gateway dispatches a `RedeemEscrow` request; `onAccept` → `withdraw()` is invoked with the beneficiary.
4. Cause the token's `transfer(beneficiary, amount)` call to internally fail-and-return-`false` (e.g., contract-level condition without revert).
5. `token.call(...)` returns `success = true` (the low-level call did not revert), so `if (!success) revert TransferFailed();` does not trigger.
6. `_orders[body.commitment][token] -= amount;` executes, and `_filled[body.commitment] = beneficiary;` finalizes the order — despite the beneficiary's token balance never increasing. The escrowed funds are now unaccounted for and unrecoverable through the normal withdrawal path.

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L452-454)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }
```

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
