## Finding: Cross-chain escrow withdrawal permanently reverts and locks remaining input tokens once any single input's escrow is fully drained

### Title
Tron `IntentGatewayV2.withdraw()` reverts on any zero-escrow token, permanently locking remaining escrowed inputs - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron variant of `IntentGatewayV2` implements cross-chain escrow release in `withdraw()` with a per-token guard, `if (_orders[body.commitment][token] == 0) revert UnknownOrder();`, that is evaluated unconditionally for every entry in `body.tokens` — including entries whose `amount` is `0`. Unlike the parallel EVM implementation (`IntentsBase._withdraw`), which explicitly skips zero-amount entries with `if (amount == 0) continue;` before touching the escrow map, the Tron path has no such guard. This exactly reproduces the bug class from the external report: a check meant to gate a single unit of custody instead gates the *entire* withdrawal, so once any one input token's escrow balance legitimately reaches zero, the whole `RedeemEscrow`/`RefundEscrow` finalize call reverts and the rest of the order's still-escrowed tokens become permanently stuck in the `IntentGatewayV2` contract.

### Finding Description
`evm/tron/contracts/apps/IntentGatewayV2.sol` `withdraw()`: [1](#0-0) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

    uint256 len = body.tokens.length;
    for (uint256 i; i < len;) {
        address token = address(uint160(uint256(body.tokens[i].token)));
        uint256 amount = body.tokens[i].amount;
        if (_orders[body.commitment][token] == 0) revert UnknownOrder();
        ...
        _orders[body.commitment][token] -= amount;
        ...
    }
    ...
}
```

Compare this to the EVM implementation in `IntentsBase._withdraw`, which is the direct counterpart on the primary EVM path: [2](#0-1) 

```solidity
for (uint256 i; i < len; i++) {
    address token = address(uint160(uint256(body.tokens[i].token)));
    uint256 amount = body.tokens[i].amount;
    if (amount == 0) continue;

    uint256 escrowed = _orders[body.commitment][token];
    if (escrowed == 0) revert UnknownOrder();
    ...
}
```

The EVM version explicitly `continue`s past zero-amount entries before checking `escrowed == 0`, precisely because a token whose escrow has already been fully consumed (e.g., by protocol-fee accounting, a prior release, or simply representing `0` on a multi-input order) is a legitimate, expected state — not an error. The Tron contract dropped this guard. Both contracts are called the same way, from `onAccept` for `RedeemEscrow`/`RefundEscrow` and from `onGetResponse` for source-side cancellation: [3](#0-2) [4](#0-3) 

`body.tokens` is built directly from `order.inputs` (the full input array of the order) in every dispatch path — fill, cancel-from-source, and cancel-from-destination: [5](#0-4) 

For any order with more than one input token, if one of those tokens' escrow balance is ever `0` at settlement time (its `_orders[commitment][token]` mapping entry reads zero — which happens naturally whenever a token's escrowed amount is fully accounted for, e.g. because that input leg was reduced to zero by a prior fee/dust computation, or because the mapping was never populated with a nonzero value for that token due to a fee-on-transfer/zero-amount edge case), the loop's `revert UnknownOrder()` fires on that iteration and reverts the *entire* transaction — even though other tokens in the same array still have positive escrow balances waiting to be released. This is functionally identical to the TermRepoLocker bug: a guard intended to validate one unit of custody instead blocks the release of unrelated, still-owed funds, because the check does not account for legitimately-zero entries.

Because `_filled[body.commitment]` is only set inside `withdraw()` (which never completes), the order can never be finalized through this path again — the escrow is permanently locked in the contract with no other route to release it (no separate token-by-token withdrawal function, no admin sweep for user-owned escrow as opposed to protocol dust).

### Impact Explanation
This produces the exact "loss/lock of funds" impact class the report calls out: cross-chain redemption or refund messages that Hyperbridge itself dispatches (`RedeemEscrow`/`RefundEscrow` via `onAccept`, or the cancellation GET response via `onGetResponse`) will unconditionally revert whenever any single input token entry has a zero escrow balance, permanently stranding all remaining escrowed input tokens for that order in the `IntentGatewayV2` contract. Solvers who filled the order lose their entitled input tokens, and users who cancelled lose their refund — with no fallback recovery mechanism in the contract.

### Likelihood Explanation
This does not require a malicious peer, relayer, or admin — it is a straightforward on-chain state condition (a zero balance for one input token among several) that legitimately arises from normal order construction and fee handling, then gets processed automatically once Hyperbridge delivers the settlement or refund message. The condition is deterministic and can be constructed by any user placing a multi-input order where the guard trips, so it is a directly triggerable, unprivileged-attacker-independent bug in the settlement path itself.

### Recommendation
Mirror the fix already present in the EVM `IntentsBase._withdraw`: skip zero-amount entries before checking the escrow balance, i.e. add `if (amount == 0) continue;` prior to the `_orders[body.commitment][token] == 0` check in the Tron `withdraw()` function, so that a legitimately-zero per-token escrow does not block the release of the order's other, still-escrowed tokens.

### Proof of Concept
1. A user places a cross-chain order with two input tokens, A and B, where token A's escrowed amount ends up `0` for this commitment (e.g., due to a fee/dust computation reducing it to zero, or any code path that leaves `_orders[commitment][A] == 0` while `_orders[commitment][B] > 0`).
2. A solver fills the order on the destination chain; the destination gateway dispatches a `RedeemEscrow` `PostRequest` back to the source chain with `body.tokens = order.inputs` (containing both A and B, A with amount `0`).
3. Hyperbridge delivers the message; the source-chain `IntentGatewayV2.onAccept` calls `withdraw(body, false)`.
4. In the loop, when `token == A`, `_orders[commitment][A] == 0` triggers `revert UnknownOrder()`, reverting the whole transaction.
5. Token B's escrow, which is fully funded and rightfully owed to the solver, is never released and remains permanently locked in the contract — the transaction can never succeed because the same zero-balance check for token A will always fire.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L588-591)
```text
            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );
```

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```

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
