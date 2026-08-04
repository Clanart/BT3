## Analysis

The referenced M-08 finding is a generic "unchecked ERC20 transfer result" bug class. Searching Hyperbridge's EVM app contracts for ERC20 escrow-release logic surfaces a clear inconsistency: the mainline intents implementation in `evm/src/apps/intentsv2/IntentsBase.sol` correctly uses OpenZeppelin's `safeTransfer` (which validates both call success *and* the ABI-decoded boolean return value), but the parallel implementation in `evm/tron/contracts/apps/IntentGatewayV2.sol` reverted to a raw low-level `.call()` pattern that only checks whether the call itself reverted — not whether the token's `transfer()` actually returned `true`.

### Title
Escrow settlement in `IntentGatewayV2.withdraw()` accepts a non-reverting `false` return from `IERC20.transfer`, permanently losing beneficiary funds while finalizing the order - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` releases escrowed intent funds after a cross-chain `RedeemEscrow`/`RefundEscrow` request is accepted. It moves tokens with a raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` and treats the transfer as successful whenever the low-level call does not revert, without decoding/validating the returned boolean.

### Finding Description
In `withdraw()`, `_filled[body.commitment]` is set unconditionally at the top of the function, and for each escrowed token: [1](#0-0) 

```
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();
```

`success` only reflects that the external call did not revert. Per EIP-20, a compliant `transfer()` is explicitly allowed to return `false` on failure instead of reverting — this is standard behavior, not "malicious token" behavior. When that happens, `success` is `true` even though zero tokens moved, so:
- `_orders[body.commitment][token] -= amount;` decrements escrow accounting as if funds were paid out,
- `_filled[body.commitment] = beneficiary;` finalizes/marks the order as settled, blocking any future retry or cancellation path,
- the beneficiary never receives the tokens.

The same unchecked pattern recurs in the `SweepDust` handler and the fee-transfer branch of the same function: [2](#0-1) [3](#0-2) 

This is exactly the class of bug the original M-08 report calls out — a transfer whose result is not validated, with a "return false" failure mode silently treated as success rather than reverting. The codebase's own mainline app contract shows the correct fix already applied elsewhere: [4](#0-3) 

which uses `safeTransfer` (checks both call success and decoded return data), proving the Tron variant is an unpatched regression of the same escrow-release code path.

### Impact Explanation
This sits directly in the bridge-custody / intent-settlement path called from `onAccept` for `RedeemEscrow`/`RefundEscrow` and from `onGetResponse` after a Hyperbridge-verified cross-chain proof: [5](#0-4) [6](#0-5) 

A silently-failed transfer causes: (1) permanent loss of the escrowed funds for the legitimate beneficiary (solver or original user), since `_filled` is set and escrow is decremented as if paid, blocking any retry; (2) a false settlement — the protocol records the order as filled/refunded even though value never moved, which is a false state acceptance on the custody ledger. This matches the bounty's "stealing or loss of funds" and "false proof/state acceptance" impact categories.

### Likelihood Explanation
Triggering this does not require a malicious peer, relayer, or admin — it only requires an order whose escrowed token is a standard, EIP-20-compliant ERC20/TRC20 that returns `false` rather than reverting on transfer failure (a common, spec-legal implementation choice, not an exotic or malicious one, and especially prevalent among TRC20 tokens on Tron, which this contract targets). Any order creator picking such a token as an input asset — or a transient failure condition (e.g., a paused/blacklist-style token state at redemption time) — silently locks the beneficiary's funds. This requires no privileged access and is reachable through the normal, permissionless order-fill/cancel lifecycle.

### Recommendation
Replace the raw `token.call(...)` + `success`-only check in `withdraw()` (and the identical pattern in the `SweepDust` handler and the fee-transfer branch) with OpenZeppelin's `SafeERC20.safeTransfer`, matching the pattern already used in `IntentsBase.sol::_withdraw`. This validates both call success and the ABI-decoded return value, reverting on any transfer that does not affirmatively return `true`.

### Proof of Concept
1. Deploy a standard-compliant ERC20/TRC20 token whose `transfer()` returns `false` (instead of reverting) when, e.g., the caller is blacklisted or balance is insufficient at call time — legal per EIP-20.
2. Create and escrow an intent order using this token as an input on the source chain via `IntentGatewayV2`.
3. Have a solver fill the order and trigger the `RedeemEscrow` (or a user trigger `RefundEscrow`) cross-chain message, which Hyperbridge delivers and verifies via ISMP.
4. At the moment `onAccept`/`onGetResponse` calls internal `withdraw()`, force the token's `transfer()` to return `false` (e.g., blacklist the gateway contract temporarily, or exhaust balance via a reentrant path) so the low-level call does not revert but yields `success = true` with an ABI-encoded `false`.
5. Observe: `_filled[commitment]` is set, `_orders[commitment][token]` is decremented to zero, `EscrowReleased`/`EscrowRefunded` is emitted — yet the beneficiary's token balance is unchanged. The order can never be retried or cancelled again, permanently locking the funds inside the contract.

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-714)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L403-409)
```text
            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```
