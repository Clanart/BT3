## Analysis

I found a direct structural analog to the GoodEntry `collect(0,0)` DoS in the Tron deployment of the Intent Gateway. The `withdraw()` function that finalizes cross-chain settlement (both `RedeemEscrow` and `RefundEscrow`) contains the exact same "check a value that isn't the value being consumed" defect that caused the GoodEntry vault to brick, and this Tron contract has *not* received the fix that the mainline EVM `IntentsBase.sol` already applies (`if (amount == 0) continue;`).

### Title
Cross-chain escrow redemption/refund permanently blocked by stale zero-balance check in `IntentGatewayV2.withdraw` (Tron) - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` iterates `body.tokens` and guards each transfer with `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` [1](#0-0) . This checks the *current escrow balance* rather than the *amount being withdrawn in this call*, mirroring the GoodEntry `collect(0,0)` bug where a downstream call reverted on a zero condition unrelated to whether the operation itself was a no-op. Once any per-token escrow slot for a commitment reaches zero (fully drained), any subsequent `RedeemEscrow`/`RefundEscrow` message or GET-response callback that still lists that token in `body.tokens` (even to redeem `TRANSACTION_FEES` or other tokens in the same batch) reverts the entire transaction with `UnknownOrder()`. This is invoked from `onAccept` for both `RedeemEscrow` and `RefundEscrow` [2](#0-1) , and from `onGetResponse` for source-side cancellation [3](#0-2) .

### Finding Description
The mainline EVM contract (`IntentsBase._withdraw`) already fixes this exact pattern:
```
if (amount == 0) continue;
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
``` [4](#0-3) 

The Tron contract's `withdraw()` lacks the `amount == 0` skip and instead unconditionally checks `_orders[body.commitment][token] == 0` for every listed token before attempting the transfer:
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
``` [5](#0-4) 

Because `_filled[body.commitment]` is only written *after* entering the function and the revert happens mid-loop, this means: whenever a settlement or refund message references any token whose escrow has already reached exactly zero for that commitment (e.g., an underflow-adjacent state from a prior partial operation, or simply a duplicate/derived retry constructed from stale order data with a zero-balance token line item), `withdraw()` reverts unconditionally — for the entire batch, including tokens that do have escrow left and the transaction fee redemption. Because `_filled` is never actually written on a revert, the commitment stays unsettled forever and the message can never successfully be replayed with the same body, permanently locking the escrowed funds exactly as in the GoodEntry case, where `collect()` reverted on a value unrelated to the intended withdrawal amount and bricked the vault's rebalancing/withdraw path.

This is functionally identical to the audited bug: a downstream state-consuming call is guarded by a check on the wrong variable (escrow balance instead of requested amount), so a legitimate, expected zero/edge-case state makes the entire settlement operation permanently revert, with no facility to skip the offending token and complete the rest of the withdrawal.

### Impact Explanation
This falls squarely within the bounty's "loss of funds" / "false state acceptance-adjacent" category for bridge custody: escrowed user or solver funds become permanently unretrievable once a `RedeemEscrow`/`RefundEscrow`/GET-response withdrawal message references a token whose escrow balance is already zero for that commitment. Since ISMP `Post` requests of this kind are dispatched with `timeout: 0` (no timeout) [6](#0-5) , there is no automatic fallback path, and the commitment can never be marked filled/refunded, so the input tokens, and the accrued transaction fees, remain locked in the contract indefinitely.

### Likelihood Explanation
The likelihood depends on whether a zero-escrow token entry can legitimately or adversarially appear in `body.tokens` for a still-outstanding commitment. This requires further code-level confirmation of how `WithdrawalRequest.tokens` is populated for `RedeemEscrow` on the destination side and whether any token list could contain a zero-amount/zero-escrow line item (the mainline codebase's explicit fix for `amount == 0` strongly suggests this scenario has already been observed/exploited or defensively patched upstream, but was never backported to the Tron contract). I was not able to fully trace the destination-side construction of `RedeemEscrow`'s `WithdrawalRequest.tokens` within the available context to confirm a concrete attacker-controlled trigger; this should be verified further, e.g., by reviewing `ExtrinsicIntents.sol`/`IntrinsicIntents.sol` `fillOrder` paths that build this struct for cross-chain settlement.

### Recommendation
Backport the fix already present in `IntentsBase.sol` to the Tron contract: skip zero-amount token entries (`if (amount == 0) continue;`) before checking/decrementing escrow, and only revert `UnknownOrder()` when a *non-zero* withdrawal amount exceeds the escrowed balance, so a stale or degenerate token entry cannot block the entire batch and permanently strand the settlement.

### Proof of Concept
Not independently reproduced end-to-end because the exact code path that could place a zero-escrow token into an inbound `WithdrawalRequest.tokens` for the Tron gateway (mirroring the "small `aBal`"/edge-case liquidity condition in the original GoodEntry PoC) needs further tracing in the order-construction/fill logic. The structural defect itself — check-on-wrong-variable causing an unconditional revert that can never be un-stuck by retry — is directly confirmed in the code shown above and is the same bug class as the GoodEntry `collect(0,0)` finding.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L593-598)
```text
            DispatchPost memory request = DispatchPost({
                dest: order.source,
                to: abi.encodePacked(instance(order.source)),
                body: body,
                timeout: 0,
                fee: options.relayerFee,
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
