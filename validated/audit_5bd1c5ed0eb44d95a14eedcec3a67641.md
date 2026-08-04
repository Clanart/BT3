### Title
Missing zero-amount skip in `withdraw()` permanently locks escrowed funds — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of the intent-settlement gateway's escrow-release function, `withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` (lines 682-721), unconditionally executes a token transfer for every entry in `body.tokens`, including entries whose `amount` is `0`. The canonical EVM implementation of the same logic, `_withdraw()` in `evm/src/apps/intentsv2/IntentsBase.sol` (lines 390-410), explicitly guards against this with `if (amount == 0) continue;`. The Tron contract is missing this guard, which is the exact bug-class described in the external report: a call that can receive a `0` value causing a downstream validation/transfer to revert and abort the entire operation.

### Finding Description
`withdraw()` is the single internal function used to release escrow for both `RedeemEscrow` and `RefundEscrow` message kinds delivered via `onAccept` [1](#0-0) , for the cross-chain cancel response via `onGetResponse` [2](#0-1) , and for the same-chain cancel path in `cancelOrder` [3](#0-2) .

Inside `withdraw()`, each token leg is checked and transferred without a zero-amount short-circuit: [4](#0-3) 

Compare this to the reference implementation used elsewhere in the same codebase, which explicitly skips zero-amount legs before touching escrow accounting or issuing a transfer: [5](#0-4) 

The corrupted value here is `body.tokens[i].amount == 0` for at least one leg of a multi-token withdrawal request. Two independent failure paths stem from this:

1. **`UnknownOrder` revert path**: the guard `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` at line 691 checks the *escrow balance*, not the withdrawal `amount`. Any token entry that was never escrowed (escrow == 0) but is nonetheless present in `body.tokens` with `amount == 0` causes the whole function — and therefore the whole `onAccept`/`onGetResponse` call — to revert.
2. **Reverting ERC20 transfer path**: even when the escrow balance for that token is non-zero, the code still issues `token.call(transfer(beneficiary, 0))` for the zero-amount leg. Tokens that reject zero-value transfers (a known category of non-conforming ERC-20s) will make this raw `.call` return `success = false`, hitting `revert TransferFailed();` at line 698 — again reverting the entire settlement.

Because this function is invoked from `onAccept`, which is the host-driven, unauthenticated-by-user entrypoint for message delivery, and because `onGetResponse`/`cancelOrder` share the exact same function, there is no alternate code path to release the escrow once this condition is hit. Any retry/redelivery of the same commitment produces the identical revert (the inputs are hashed into the deterministic `commitment`, so the message body/amounts do not change on retry), so the escrowed tokens for that order become **permanently stuck** — a straightforward loss-of-funds condition reachable through ordinary order construction/settlement, not through a malicious relayer, prover, or admin.

### Impact Explanation
This falls squarely within the accepted impact classes: **loss/lock of bridged/escrowed funds**. `withdraw()` is the only settlement primitive for both the redeem (solver payout) and refund (user cancellation) flows on this chain's `IntentGatewayV2`. A single zero-amount token leg in a legitimate withdrawal request — e.g., a partial-fill order whose escrow for a given input token is already fully consumed/zero for one of several tokens, or an order that legitimately includes a token with amount 0 in `order.inputs`/derived legs — deterministically and permanently blocks settlement for that commitment. Because `_filled` is only ever set as a side effect inside the reverting call, the order is stuck in limbo indefinitely: it can never be marked filled/cancelled and the user's or solver's escrow can never be withdrawn.

### Likelihood Explanation
No privileged actor, malicious relayer, or compromised prover is required — the condition is triggered purely by data that a normal order/message can legitimately contain (a zero-amount token leg), consistent with the off-chain solver logic elsewhere in this codebase that explicitly anticipates and emits zero-amount legs for index alignment (`sdk/packages/simplex/src/strategies/fx.ts`, "the gateway skips solverAmount == 0 legs"). This shows the zero-amount-leg case is an expected, reachable input in production intent flows, not a corner case that requires attacker collusion.

### Recommendation
Mirror the guard already present in `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw`: in `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw`, add `if (amount == 0) continue;` immediately after reading `amount` and before the `_orders[...] == 0` check and the token transfer, so zero-amount legs are skipped instead of aborting the whole settlement.

### Proof of Concept
1. Construct (or arrive at, via partial fill / protocol-fee rounding) a `WithdrawalRequest.tokens` array containing at least one `TokenInfo` with `amount == 0` for a token whose corresponding `_orders[commitment][token]` entry is `0` (never escrowed, or already fully drained by a prior partial withdrawal).
2. Deliver this via `onAccept` (RedeemEscrow/RefundEscrow) or trigger it via `onGetResponse`/`cancelOrder` same-chain path.
3. `withdraw()` hits `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` at [6](#0-5)  and the entire call reverts.
4. Because the commitment/message content is deterministic, any retry (relayer redelivery of the same ISMP message, or repeated call to `cancelOrder`/`onGetResponse`) reproduces the identical revert, permanently locking all escrow tied to that commitment — funds cannot be redeemed or refunded through any other function in the contract.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L686-705)
```text
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
