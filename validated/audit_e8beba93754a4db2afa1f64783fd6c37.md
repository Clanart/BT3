I found a direct analog of the "ignored return value" bug class in the Tron-specific `IntentGatewayV2` contract, which handles cross-chain intent escrow and settlement.

### Title
Unchecked ERC20 return value in escrow settlement allows fund loss/insolvency for non-reverting tokens - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2` (built for Tron, where USDT-TRC20 and similar tokens are the dominant standard and are known to not strictly follow ERC20 return-value semantics) uses raw low-level `.call()` invocations of `IERC20.transfer` and only checks the outer call `success` flag, never decoding/validating the actual boolean return value of the token. This is the exact same broken invariant as the reported Amp.sol issue: a token that signals failure by returning `false` (instead of reverting) will make the low-level call report `success = true`, so the code proceeds as if the transfer succeeded.

### Finding Description
In `placeOrder`, when an order uses `predispatch`, sweep transfers from the `CallDispatcher` back to the gateway are built as raw calldata and routed through `ICallDispatcher.dispatch`: [1](#0-0) 

`CallDispatcher.dispatch` only checks the outer `(bool success, bytes memory result)` of the low-level call and reverts on `!success`, but never inspects `result` to confirm the token's own boolean return value: [2](#0-1) 

Critically, escrow accounting `_orders[commitment][token] += reducedInputs[i].amount;` is incremented in the same loop that builds the sweep call, *before* `ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls))` is even executed: [3](#0-2) 

The same unchecked pattern recurs in `withdraw`, where escrow is decremented and marked as redeemed regardless of whether the token itself signaled success: [4](#0-3) 

and in the dust-sweep path: [5](#0-4) 

For any ERC20 token deployed on Tron that returns `false` on a failed `transfer` rather than reverting (a widely known non-standard-but-legal ERC20 behavior, and precisely the risk called out in the original report about arbitrary token implementations), the outer `.call` succeeds, `success == true`, and the code treats the transfer as completed.

### Impact Explanation
Because `_orders[commitment][token]` escrow bookkeeping is credited based solely on the outer call succeeding rather than the actual token balance movement, an attacker can place/fill orders with a non-compliant ERC20 whose `transfer` silently fails (returns `false`) at the sweep step. The gateway then believes it holds escrowed balance it never actually received, while other legitimate orders' escrow for the same token sits in the shared contract balance. When `withdraw` is later invoked for the phantom escrow entry, it pays the phantom-order beneficiary out of the pool of tokens that actually belong to other users' real escrowed orders — a direct fund-loss/insolvency and wrong-beneficiary payout, matching "stealing or loss of funds" and "wrong beneficiary or amount" impact categories.

### Likelihood Explanation
This requires only listing/using a non-standard ERC20 token (return-false-on-failure) as an order's input/output asset on the Tron intents deployment — no relayer, prover, or admin compromise is needed. Any unprivileged user can construct such an order via the public `placeOrder`/`fillOrder`/`withdraw` entrypoints, and the only variable is the token's non-standard transfer semantics, a property intrinsic to the token contract itself, not an assumption about a malicious actor in the bridge's trust model.

### Recommendation
Replace raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns with OpenZeppelin's `SafeERC20.safeTransfer`/`safeTransferFrom` (already used elsewhere in the same file, e.g. via `safeTransferFrom` in the non-predispatch branch) so that both the outer call success and the token's own boolean return value (when present) are validated before escrow accounting is mutated. Additionally, `CallDispatcher.dispatch` should decode and validate ERC20 return data for transfer-selector calls, or escrow crediting in `placeOrder` should be derived from a `balanceOf` before/after delta (as already done in the non-Tron `evm/src/apps/IntentGatewayV2.sol`) rather than trusting the raw call outcome.

### Proof of Concept
1. Deploy/list a non-standard ERC20 token `T` where `transfer` returns `false` on failure instead of reverting (legal per the ERC20 spec, and common historically on Tron/TRC20-style tokens).
2. Attacker calls `placeOrder` with `predispatch.call` and `predispatch.assets` set up such that the dispatcher ends up with insufficient balance of `T` for the sweep, or route the sweep call through a token that simply returns `false` under some condition.
3. The `transferCalls` sweep to move `T` from `dispatcher` back to the gateway executes via `ICallDispatcher.dispatch`; because the token returns `false` rather than reverting, `CallDispatcher` sees `success = true` and does not revert.
4. `_orders[commitment][T] += reducedInputs[i].amount` was already incremented in the same loop before the dispatch call, so the gateway's escrow ledger now reflects tokens it never actually received.
5. Attacker (or an accomplice acting as the order's beneficiary) triggers `withdraw` for this commitment; the gateway pays out `T` to the beneficiary from its actual token balance — which is backed only by other legitimate orders' escrowed `T`, not by this attacker's non-existent deposit — draining funds that belong to other users.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L421-443)
```text
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
```

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-714)
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
```

**File:** evm/src/utils/CallDispatcher.sol (L44-62)
```text
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
    }
```
