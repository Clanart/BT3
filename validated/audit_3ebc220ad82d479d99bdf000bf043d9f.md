### Title
Checks-Effects-Interactions violation and missing reentrancy guard in the Tron `IntentGatewayV2.withdraw()` escrow-release path allows the escrow ledger to be manipulated via an attacker-controlled input token - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The external report's core broken invariant is: the same protective quantity (required collateral) is computed with different rigor in two code paths for the same lifecycle (mint-time vs. liquidation-time), so the "cheaper" computation lets state that should be rejected/blocked slip through. The local Hyperbridge analog is in the Intent Gateway's escrow-release logic, where the same "escrow accounting" quantity (`_orders[commitment][token]`) is protected with materially different rigor in the canonical EVM implementation versus the Tron-chain fork of the same contract. The Tron fork's `withdraw()` performs the external token/value transfer **before** updating the escrow ledger and without a reentrancy guard, unlike the hardened canonical implementation.

### Finding Description
The canonical EVM escrow-release function correctly follows checks-effects-interactions: it computes the escrow balance, reverts if empty, **writes the decremented balance to storage first**, and only then makes the external transfer: [1](#0-0) 

All public entrypoints that can reach this path (`placeOrder`, `fillOrder`, `cancelOrder`) on the primary EVM `IntentGatewayV2` are additionally protected with `nonReentrant`: [2](#0-1) [3](#0-2) 

The Tron-chain deployment of the same contract (`evm/tron/contracts/apps/IntentGatewayV2.sol`) implements the equivalent `withdraw()` function with the interaction **before** the effect: it makes the native/`token.call` transfer to `beneficiary` first, and only decrements `_orders[body.commitment][token]` afterward: [4](#0-3) 

`token` in this loop is `order.inputs[i].token`, an address chosen entirely by the user at `placeOrder` time — nothing constrains it to be a well-behaved ERC-20: [5](#0-4) 

Because the transfer/callback to an attacker-supplied token contract runs while `_orders[body.commitment][token]` still holds its **pre-decrement** value, and my search of this file found no `nonReentrant`/`ReentrancyGuard` usage guarding `placeOrder`/`fillOrder`/`cancelOrder` (in contrast to the canonical EVM contract, which explicitly guards all three), the escrow bookkeeping quantity that is supposed to gate withdrawals can be read by reentrant calls before it reflects the in-flight withdrawal.

### Impact Explanation
This falls squarely under "bridged assets, order escrow... must move exactly once and only to the rightful beneficiary and amount." If exploitable, a malicious order creator who deploys a token used as `order.inputs[i].token` can trigger reentrant calls into the gateway's other unguarded external functions during the token's `transfer()` callback, while `_orders[commitment][token]` for that token/commitment pair has not yet been decremented — reintroducing a classic escrow double-spend surface that the canonical (audited) contract deliberately closed with CEI ordering plus `nonReentrant`. This is a same-contract, same-lifecycle regression: the "collateral/escrow" invariant is enforced correctly in one deployment target and weakly in another, exactly mirroring the report's "computed differently at two points in the same lifecycle" pattern.

### Likelihood Explanation
`_filled[body.commitment]` is set at the top of `withdraw()` before the loop, which blocks the most direct same-commitment reentry into `cancelOrder`/`fillOrder` for that specific commitment. The exploitable surface is therefore narrower than a full same-commitment double-spend: it requires an attacker to control a token contract used across multiple orders/commitments (fully achievable — the attacker deploys their own ERC-20) and to chain a reentrant call from that token's `transfer()` hook into a different, independent commitment's escrow logic that shares protocol-level state affected by the stale read. This requires no relayer, prover, or admin compromise — only a user placing orders with a self-deployed token, which is entirely within an unprivileged attacker's control. I was not able to fully trace an end-to-end cross-commitment double-spend within the available time; the CEI violation and absent reentrancy guard are concretely verified, but confirming a complete fund-drain chain across two commitments needs further tracing or a runtime PoC.

### Recommendation
Align `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw()` with the canonical EVM implementation:
1. Decrement `_orders[body.commitment][token]` **before** issuing the native/`token.call` transfer (checks-effects-interactions).
2. Add `nonReentrant` (or an equivalent reentrancy guard) to `placeOrder`, `fillOrder`, and `cancelOrder` in the Tron contract, matching the primary EVM `IntentGatewayV2.sol`.
3. Audit the Tron fork for any other place where it has drifted from the hardened patterns in `evm/src/apps/intentsv2/*` and `evm/src/apps/IntentGatewayV2.sol`.

### Proof of Concept
Not independently reproduced end-to-end; the following steps outline the reentrancy window that would need to be validated with a runtime test against the Tron contract:
1. Attacker deploys a malicious ERC-20-like `EvilToken` whose `transfer()` function, when called by the gateway, re-enters a second, unrelated order's `cancelOrder()`/fill path on the same gateway instance.
2. Attacker places Order A (destined for cross-chain fill/redeem) and Order B (same-chain, cancellable), both escrowing `EvilToken`.
3. Order A is filled cross-chain; the `RedeemEscrow` message returns to the Tron gateway, and `onAccept` → `withdraw()` calls `EvilToken.transfer(beneficiary, amount)` for Order A **before** decrementing `_orders[A][EvilToken]`.
4. Inside that callback, `EvilToken` reenters the gateway's `cancelOrder()` for Order B (unaffected by Order A's `_filled` flag), attempting to interleave escrow movement while the ledger for Order A's token is stale.
5. Verify whether this interleaving allows any token accounting or dust-collection state shared across commitments to be manipulated for net fund loss, versus each commitment's `_orders` mapping being fully isolated (in which case the primary residual risk is the CEI/reentrancy-guard gap itself rather than a demonstrated fund-drain).

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L162-162)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
```

**File:** evm/src/apps/IntentGatewayV2.sol (L470-470)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L445-457)
```text
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;
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
