## Analysis

The Linea report's core broken invariant: **the pull-side transfer function (`transferFrom`) is shared by ERC-20 and ERC-721 (same selector), but the push-side transfer function (`transfer`) is ERC-20-only.** A token that satisfies the pull path can be escrowed, then permanently stuck because the payout path calls a function the token doesn't implement.

I found a structurally identical pattern in `IntentGatewayV2`'s escrow accounting: `placeOrder` accepts an arbitrary caller-supplied `token` address and infers the deposited "amount" from a balance-diff around `safeTransferFrom`, and `_withdraw` unconditionally releases that same `token`/`amount` pair using `safeTransfer`. Because `transferFrom(address,address,uint256)` is selector-compatible between ERC-20 and ERC-721, an ERC-721 "amount" (`tokenId`) sails through escrow, but `transfer(address,uint256)` — which ERC-721 never implements — reverts every future release for that order, permanently locking whatever else is escrowed under that commitment. [1](#0-0) [2](#0-1) 

### Title
Order inputs accepted via `transferFrom`-compatible ERC-721 tokens permanently lock escrow because release uses ERC-20-only `safeTransfer` - (File: evm/src/apps/intentsv2/IntentsBase.sol)

### Summary
`IntentGatewayV2.placeOrder` treats `order.inputs[i].token` as an opaque address and escrows it purely via `IERC20(token).safeTransferFrom(msg.sender, address(this), amount)`, then re-derives the escrowed `amount` from the gateway's `balanceOf` delta. Because ERC-721's `transferFrom(address,address,uint256)` shares the exact selector with ERC-20's, and ERC-721's `balanceOf(address)` also matches ERC-20's, a token identified by an ERC-721 contract with a `tokenId` in place of `amount` passes escrow silently. Every release path (`_withdraw` in `IntentsBase.sol`, used by fill, refund, and cancel) unconditionally calls `IERC20(token).safeTransfer(beneficiary, amount)`, which requires `transfer(address,uint256)` — a function ERC-721 never implements. That call reverts, and because `_withdraw` loops over *all* tokens for a commitment in one transaction, the revert blocks release of every other (legitimately fungible) asset escrowed under the same order commitment.

### Finding Description
Escrow intake in `placeOrder`:
```solidity
uint256 balBefore = IERC20(token).balanceOf(address(this));
IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
``` [1](#0-0) 

`safeTransferFrom` performs a low-level call to `transferFrom(address,address,uint256)` (selector `0x23b872dd`). ERC-721's `transferFrom(address from, address to, uint256 tokenId)` uses the identical selector and signature shape, so if `order.inputs[i].amount` is a `tokenId` the caller owns/has approved, this call succeeds. `balanceOf(address)` also matches (ERC-721 returns NFT count), so the balance-diff overwrite silently rewrites `order.inputs[i].amount` from the intended `tokenId` to `1` (the NFT count delta) — destroying the only on-chain record of which token was actually escrowed.

Escrow release in `IntentsBase._withdraw` (used for `fillOrder`/settlement, refunds, and cancellations across both same-chain and cross-chain flows):
```solidity
_orders[body.commitment][token] = escrowed - amount;
if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    IERC20(token).safeTransfer(beneficiary, amount);
}
``` [2](#0-1) 

`safeTransfer` calls `transfer(address,uint256)` (selector `0xa9059cbb`), which ERC-721 does not implement and has no fallback for. The call reverts unconditionally, and since this is inside a `for` loop iterating `body.tokens`, the entire `_withdraw` transaction reverts — for every token type bundled in that same commitment, not just the ERC-721 one. The same defect exists in the Tron variant's `withdraw`, which uses a raw low-level call with the `IERC20.transfer.selector` and reverts on `!success`: [3](#0-2) 

No guard anywhere in `placeOrder`, `fillOrder`, `cancelOrder`, or `_withdraw` validates that `token` actually exposes ERC-20 `transfer`/`decimals` semantics before accepting it into escrow — exactly the gap the Linea report recommends closing by checking for `decimals()` support before allowing the asset to be bridged.

### Impact Explanation
Once an ERC-721-shaped `token`/`tokenId` pair is escrowed under a commitment, every subsequent call to release that commitment's escrow (`fillOrder` on the destination triggering the cross-chain `RedeemEscrow` settlement, `cancelOrder` refund, or same-chain partial/full fill withdrawal) will revert on the `safeTransfer` step. This is a permanent, protocol-level loss/lock of funds: any other ERC-20 assets bundled into the same order's `inputs` (multiple input tokens are supported) become unrecoverable through normal contract logic, matching the bounty's "stealing or loss of funds" / logic-attack impact class. Recovery would require a contract upgrade, identical to the Linea finding's conclusion.

### Likelihood Explanation
The path requires no privileged actor, relayer, prover, or governance action — any unprivileged account can call `placeOrder` with a `token` address pointing at an ERC-721 contract and a `tokenId` they own/approved as the `amount`. The escrow-side call succeeds silently (no revert, no type check), so the order is accepted and only fails destructively later, at release time, when the failure is much harder to detect and impossible to route around without an upgrade.

### Recommendation
Before accepting a token into escrow, positively verify ERC-20 semantics — e.g., require a successful `IERC20Metadata(token).decimals()` call (which ERC-721 contracts do not implement) as a canary, as the Linea team's own remediation does — and reject the order otherwise. Additionally, `_withdraw` should not let a single token's transfer failure abort the release of the remaining tokens in the same commitment; isolating per-token transfer failures (or pre-validating token contracts at `placeOrder` time) prevents one malformed asset from freezing an entire order's legitimate escrow.

### Proof of Concept
1. Deploy a minimal ERC-721-like contract `Evil721` that implements `transferFrom(address,address,uint256)` and `balanceOf(address)` (standard ERC-721), owned tokenId `T` held by `attacker`.
2. `attacker` calls `placeOrder` with `order.inputs = [{token: address(Evil721), amount: T}]` and any valid output leg; `attacker` has approved the gateway for `T` via `approve`/`setApprovalForAll`. `safeTransferFrom(attacker, gateway, T)` succeeds (selector-compatible), and `order.inputs[0].amount` is overwritten to `1` by the balance-diff logic.
3. A solver fills the order (same-chain or cross-chain). When `_withdraw` is invoked to release the escrow to the solver/beneficiary, `IERC20(Evil721).safeTransfer(beneficiary, 1)` reverts (`transfer` selector not implemented) — the entire fill/settlement transaction reverts.
4. Same result for `cancelOrder`'s refund path — the order's escrow (NFT and any co-escrowed ERC-20 inputs) is permanently stuck; no on-chain function can release it.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L282-298)
```text
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

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
