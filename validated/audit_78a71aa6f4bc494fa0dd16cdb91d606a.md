## Finding

The Tron fork of the Intent Gateway reintroduces the exact unsafe-transfer pattern from the seed report, despite the main EVM implementation already using `SafeERC20`.

### Title
Escrow withdrawal silently accepts failed ERC-20 transfers via unchecked low-level `.call` return data - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2` on Tron imports and uses `SafeERC20` for inbound `transferFrom` calls, but for outbound settlement — `withdraw()` and the `SweepDust` handler — it reverts to constructing a raw low-level call to the `transfer` selector and only checks that the call itself did not revert, never decoding/validating the boolean return value.

### Finding Description
In `withdraw()`, escrowed order tokens and transaction fees are released with: [1](#0-0) 

```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```

The identical pattern appears in the `SweepDust` handler: [2](#0-1) 

`success` here only reflects whether the low-level `call` reverted — it says nothing about the ABI-encoded `bool` the ERC-20 `transfer()` function is supposed to return. Any ERC-20 whose `transfer()` returns `false` on failure instead of reverting (a widely used pattern for "weird" tokens, e.g. tokens with fee/pausable/blacklist semantics, or centralized stablecoins used in real deployments) will cause this code to treat a **failed transfer as a successful one**. This is precisely the invariant break identified in the seed report: unchecked/unsafe transfer treats `false`-returning failures as success.

Crucially, before this transfer, `withdraw()` has already performed the state mutations that make the operation irreversible: [3](#0-2) 

`_filled[body.commitment] = beneficiary` is set and `_orders[body.commitment][token] -= amount` is decremented regardless of whether the token transfer actually moved value. Once `_filled` is set, the commitment can never be retried (guarded by the `Filled()` check elsewhere in the fill/refund flow), and once `_orders[...]` is decremented, the escrow slot is drained. So a `false`-returning token transfer results in permanent loss of the escrowed value: the beneficiary (solver being paid, or user being refunded) never receives the tokens, but the protocol's internal accounting is fully closed out as if the transfer succeeded, since there is no revert and no way to recover the wiped escrow entry.

This differs from the sibling non-Tron implementation (`evm/src/apps/IntentGatewayV2.sol`, `evm/tron/contracts/apps/IntentGatewayV2.sol`'s own predispatch section) which correctly uses `IERC20(token).safeTransferFrom(...)` for inbound legs — the vulnerability is isolated to the *outbound* settlement path (`withdraw`, `SweepDust`) that was implemented with a bespoke low-level call instead of `SafeERC20.safeTransfer`.

### Impact Explanation
This directly causes permanent loss of escrowed bridged funds: solvers filling cross-chain intents, or users being refunded on cancellation/timeout, can have their `_orders` escrow balance zeroed and their claim marked `Filled` without ever receiving the underlying tokens, whenever the input/fee token used in the order returns `false` on a failed `transfer()` (e.g., due to internal token-level restrictions such as blacklists, pause states, or transfer caps) rather than reverting. This matches the required impact class of stealing/loss of funds and false-state acceptance in escrow settlement — funds do not move exactly once to the rightful beneficiary.

### Likelihood Explanation
The path is reachable by any ordinary, unprivileged flow: a user creates an intent order (choosing the input/fee token, which is not restricted to a fixed allowlist in this contract), a solver fills it, and settlement/refund invokes `withdraw()` on Tron. No malicious relayer, prover, admin, or governance action is required — only that the escrowed token can return `false` from `transfer()` under some condition (e.g., blacklist, insufficient special-purpose balance, pause flag), which is a realistic and common ERC-20 behavior on this ecosystem, consistent with the same weird-token risk explicitly called out in the seed report.

### Recommendation
Replace the raw low-level `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` and the `SweepDust` handler with `SafeERC20.safeTransfer()`, which is already imported and used elsewhere in this same contract (`using SafeERC20 for IERC20;`), so that both non-boolean-returning and `false`-returning tokens are handled correctly and any failed transfer reverts the entire escrow release/refund/sweep before state is mutated.

### Proof of Concept
1. Deploy/select a token whose `transfer()` implementation returns `false` on failure instead of reverting (e.g. a fee/compliance token, or any ERC-20 following the pattern flagged in the seed Sherlock report).
2. Create and fill a cross-chain intent order (or trigger a refund) using this token as an input/fee token, following the normal order lifecycle in `IntentGatewayV2.sol`.
3. Cause the token's `transfer()` to return `false` for the withdraw call (e.g., beneficiary temporarily blacklisted, or a business-logic condition inside the token causing the transfer to no-op) instead of reverting.
4. Observe `withdraw()` at [4](#0-3)  proceeds: `success` is `true` (the call itself didn't revert), so `_orders[body.commitment][token]` is decremented and `_filled[body.commitment]` is set to the beneficiary, and `EscrowReleased`/`EscrowRefunded` is emitted — even though the beneficiary's token balance never changed.
5. The beneficiary has permanently lost their escrowed funds with no retry path, since the commitment is now marked filled and the escrow entry is zeroed.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-713)
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
```
