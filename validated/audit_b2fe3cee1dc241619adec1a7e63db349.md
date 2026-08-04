## Title
`_withdraw`/`withdraw` release escrowed tokens without validating `amount` against the escrowed balance - ([File: evm/src/apps/intentsv2/IntentsBase.sol], [File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The reported class of bug ("subtraction assumes the left operand is always `>= amount`, but the code only checks for `== 0`") reproduces in Hyperbridge's IntentGatewayV2 escrow-release path. `_withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol:390-425`) and its Tron sibling `withdraw` (`evm/tron/contracts/apps/IntentGatewayV2.sol:682-721`) both guard the per-token escrow decrement with `if (escrowed == 0) revert UnknownOrder();`, never checking `escrowed >= amount` before subtracting/transferring `amount`. [1](#0-0) [2](#0-1) 

### Finding Description
In `_withdraw`, for each `body.tokens[i]`:
```
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;
```
Only the zero case is rejected; `amount` (taken directly from the `WithdrawalRequest.tokens[]` array supplied to the function) is never bounded against `escrowed`. In the Tron variant the ordering is worse: the external token/native transfer of `amount` to `beneficiary` happens *before* `_orders[commitment][token] -= amount`, so if `amount` could ever exceed `escrowed`, funds would leave the contract first and the accounting decrement would only revert afterward (Solidity 0.8 checked arithmetic reverts the whole call, but this still shows the accounting invariant `escrowed >= amount` is never enforced structurally — only relied upon by chance of revert-on-underflow rather than an explicit guard).

`WithdrawalRequest.tokens[]` values are attacker-influenced through two paths that reach `_withdraw`/`withdraw` without cross-checking the *reduced* (fee-adjusted) escrow amount recorded at `placeOrder` time:
1. `onAccept` → `RedeemEscrow`/`RefundEscrow`, which decodes `WithdrawalRequest` straight from the ISMP request body after only `authenticate(...)` (peer/module identity check, not an amount check).
2. `cancelOrder`'s same-chain and cross-chain paths, which build `WithdrawalRequest` from `order.inputs` — the *original* (pre-protocol-fee) input amounts — while `_orders[commitment][token]` was populated during `placeOrder` with `reducedInputs[i].amount` (post-fee amount, strictly smaller). See `placeOrder`'s escrow bookkeeping at `_orders[commitment][token] += reducedInputs[i].amount` versus `order.inputs[i].amount` reused verbatim in the withdrawal body construction. [3](#0-2) [4](#0-3) [5](#0-4) 

Because `withdraw`/`_withdraw` never re-derives `amount` from the escrowed record itself (it trusts the caller-supplied `body.tokens[i].amount` verbatim, only checking presence via `escrowed == 0`), any code path that can construct a `WithdrawalRequest` whose `amount` differs from the actually-escrowed reduced amount produces either (a) a permanent revert that locks the whole order's escrow (loss/lock of funds — same-chain `cancelOrder` composes `order.inputs`, the pre-fee amount, directly into `withdraw`, so any order with `protocolFeeBps > 0` cancelled locally will always attempt to refund more than was escrowed and revert, permanently freezing the user's escrowed funds since `_filled` may already be set before the failing transfer/decrement depending on ordering) or (b) if the guard is ever weakened/removed in a future forked/derivative build (e.g., partial-fill code paths that compute proportional withdrawal amounts), a genuine underflow/overpay.

### Impact Explanation
This falls squarely under "loss or lock of funds" in the bounty scope: escrowed user tokens/native currency become permanently stuck because the only sufficiency check is `escrowed == 0`, not `escrowed >= amount`, and the amount plumbed into the withdrawal body (`order.inputs`, pre-fee) systematically diverges from the amount actually escrowed (`reducedInputs`, post-fee) whenever `protocolFeeBps > 0`. This is a self-inflicted denial of a user's own funds triggerable by the unprivileged order owner simply calling `cancelOrder` on their own same-chain order.

### Likelihood Explanation
High for any deployment where `_params.protocolFeeBps > 0` or a `_destinationProtocolFees[destination] > 0` is configured (documented as a normal, non-privileged operational state — governance sets destination fees via `UpdateParams`). Any user who places an order under fee and later cancels it on the same chain hits this path deterministically; no relayer, prover, or admin collusion is required.

### Recommendation
- In `_withdraw`/`withdraw`, replace the `escrowed == 0` check with `if (escrowed < amount) revert UnknownOrder();` (or a dedicated `InsufficientEscrow` error) before performing the transfer/decrement, and perform the internal accounting decrement before any external call (checks-effects-interactions).
- Ensure `cancelOrder`'s `WithdrawalRequest.tokens` is built from the same post-protocol-fee amounts that were actually escrowed in `placeOrder` (i.e., reuse `reducedInputs`, not raw `order.inputs`), for both the same-chain and cross-chain (GET-proof and RefundEscrow POST) cancellation paths.
- Add a regression test that places an order with a non-zero protocol fee and then cancels it same-chain, asserting the refund succeeds and returns exactly the escrowed (post-fee) amount rather than reverting or under/over-paying.

### Proof of Concept
1. Governance sets a non-zero protocol fee for a destination (or the global `_params.protocolFeeBps > 0`), a standard, non-privileged-attacker-independent runtime state.
2. A user calls `placeOrder` with `order.inputs = [{token: T, amount: 1000}]`, `destination == source` (same-chain order). `placeOrder` computes `reducedInputs[0].amount = 1000 - fee` and stores `_orders[commitment][T] = 1000 - fee`.
3. The same user calls `cancelOrder(order, options)` before it is filled. Because `isSameChain` is true, `IntentsBase._withdraw`/Tron `withdraw` is invoked directly with `body.tokens = order.inputs` — i.e., `amount = 1000` (the *original*, pre-fee amount), not `1000 - fee`.
4. Inside `withdraw`: `escrowed = _orders[commitment][T] = 1000 - fee` (non-zero, so the guard passes) but `amount = 1000 > escrowed`. The subsequent `_orders[commitment][token] -= amount` (or the Tron variant's transfer-then-subtract) underflows and reverts under Solidity 0.8 checked arithmetic, causing the entire `cancelOrder` transaction — including `_filled[commitment] = beneficiary` — to roll back. The order can never be successfully cancelled through this path again in the same state, and because the same amounts are reused on every retry, the user's escrowed `1000 - fee` tokens are permanently stuck in the contract with no code path able to release them.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-410)
```text
        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L435-435)
```text
                _orders[commitment][token] += reducedInputs[i].amount;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L527-530)
```text
            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L588-591)
```text
            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );
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
