### Title
Missing zero-amount guard in Tron `IntentGatewayV2.withdraw()` allows revert-on-zero-transfer tokens to permanently block escrow settlement - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron fork of `IntentGatewayV2` implements order escrow release/refund in `withdraw()`, which loops over `body.tokens` and unconditionally calls `token.call(transfer(...))` for every entry, including zero-amount ones. The canonical EVM implementation (`IntentsBase._withdraw`, `evm/src/apps/intentsv2/IntentsBase.sol`) explicitly guards this with `if (amount == 0) continue;`, but this guard is absent from the Tron variant.

### Finding Description
`withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` (lines 682-721) is invoked from `onAccept()` (`onlyHost`) after a `RedeemEscrow`/`RefundEscrow` ISMP request is authenticated and delivered — i.e. this is the normal, proof-verified settlement path for a filled or cancelled cross-chain intent order, not a malicious-peer scenario.

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

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
        unchecked { ++i; }
    }
    ...
}
``` [1](#0-0) 

There is no `if (amount == 0) continue;` before the ERC20 transfer, unlike the reference implementation:
```solidity
uint256 amount = body.tokens[i].amount;
if (amount == 0) continue;
``` [2](#0-1) 

If any of the order's escrowed tokens is one that reverts on a zero-value `transfer` (a documented weird-ERC20 behavior, e.g. some legacy tokens), and the corresponding `body.tokens[i].amount` is `0` for that token in the withdrawal request (which can legitimately occur for a multi-token order where one leg settles to zero — e.g. a partially-priced output, a fee-adjustment path, or an order deliberately crafted by the filler/solver with a zero-value leg for one token), the low-level `.call` will return `success == false`, and the function reverts with `TransferFailed()`. Because `_filled[body.commitment] = beneficiary` is written before the loop and the whole transaction reverts atomically, `_orders[body.commitment][token]` balances are never decremented and the beneficiary can never successfully claim this order — every future retry of `withdraw()` for the same commitment hits the exact same zero-amount token and reverts identically.

### Impact Explanation
This is a protocol-liveness/fund-lock bug matching the bounty's "loss of funds" and "logic attacks" categories: escrowed assets for the affected order become permanently unclaimable because the settlement path (`onAccept` → `withdraw`) can never complete successfully for that commitment. Both `RedeemEscrow` (fill payout) and `RefundEscrow` (cancellation refund) paths are affected, so a filler could be denied their earned payout, or a user could be denied a legitimate refund — with no code path to bypass the reverting token leg. Since `_orders[commitment][token]` is only ever decremented inside this same reverting loop, the escrow entry stays permanently locked at its pre-withdrawal balance.

### Likelihood Explanation
This requires only a legitimate multi-token order/withdrawal in which one token leg amount is zero and that token type reverts on zero-value transfers — a known and documented ERC-20 quirk (`weird-erc20`). No malicious relayer, prover, or governance actor is needed; the corrupted value is a routine `body.tokens[i].amount == 0` combined with the properties of an arbitrary permissionless-listed ERC-20 token in the order. Since `IntentGatewayV2` supports arbitrary tokens per order without an explicit allow-list check in `withdraw()`, this is directly reachable through the normal settlement flow. Likelihood is limited by requiring a zero-valued token leg to occur in practice, which depends on upstream order-construction/pricing logic (not verified in this review) — but the missing guard is a clear divergence from the already-fixed pattern in the main `IntentsBase._withdraw()`.

### Recommendation
Mirror the existing `IntentsBase._withdraw()` guard in the Tron `IntentGatewayV2.withdraw()`:
```solidity
uint256 amount = body.tokens[i].amount;
if (amount == 0) {
    unchecked { ++i; }
    continue;
}
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
```
This prevents a zero-value leg from ever reaching the external `transfer` call, eliminating the revert-on-zero-transfer denial-of-service/fund-lock vector, consistent with the already-patched pattern in the canonical EVM implementation.

### Proof of Concept
1. A cross-chain intent order is created/filled where the `WithdrawalRequest.tokens` array includes at least two tokens, one of which (`tokenA`, a standard ERC-20) has `amount > 0`, and a second (`tokenB`, a token that reverts on `transfer(to, 0)`) has `amount == 0` for this particular commitment (e.g. resulting from partial pricing/fee-adjustment logic upstream).
2. Hyperbridge delivers the authenticated `RedeemEscrow`/`RefundEscrow` POST request to the Tron `IntentGatewayV2`; `onAccept` calls `withdraw(body, isRefund)`. [3](#0-2) 
3. Inside the loop, when `i` reaches `tokenB`, `amount == 0`, and `tokenB.call(transfer(beneficiary, 0))` returns `success == false` due to the token's revert-on-zero-transfer behavior.
4. `withdraw()` reverts with `TransferFailed()`; the entire `onAccept` call fails, so `_orders[commitment][tokenA]` and `_orders[commitment][tokenB]` are never cleared and `_filled[commitment]` is never durably set.
5. Any subsequent retry of the same request hits the identical zero-amount `tokenB` leg and reverts identically — the beneficiary's `tokenA` funds (and any native/fee amounts in the same order) are permanently locked in escrow with no recovery path.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L396-398)
```text
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;
```
