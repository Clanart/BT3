## Finding

### Title
Unchecked ERC20 boolean return in escrow withdrawal permanently burns escrow accounting without delivering funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` on the Tron EVM variant releases escrowed order funds using a raw low-level `.call` to the token's `transfer` function, and only checks that the *call itself did not revert* — it never decodes the ABI-encoded boolean return value. This is the exact bug class from the external report ("unchecked data … result from the transferFrom function is unchecked so if the token will not revert and returns false the execution will continue"), just on the withdrawal side of intent settlement instead of the deposit side.

### Finding Description
`withdraw()` is invoked from `onAccept` when Hyperbridge delivers a `RedeemEscrow`/`RefundEscrow` message after a same-chain/cross-chain intent fill or cancellation: [1](#0-0) 

For every ERC20 input token, the contract does:

```solidity
(bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
if (!success) revert TransferFailed();

_orders[body.commitment][token] -= amount;
```

`success` here only reflects whether the external call reverted, not whether the token's `transfer` returned `true`. Per the original ERC20 spec (and many real-world/legacy tokens still implement this), `transfer` is permitted to return `false` on failure without reverting. In that case `success == true` (the call executed and returned data), the code proceeds past the guard, and `_orders[body.commitment][token]` is decremented as if the beneficiary was paid — even though no tokens moved. The same unchecked pattern also appears in the `SweepDust` branch and the fee-redemption branch of the same function: [2](#0-1) [3](#0-2) 

This directly mirrors the report's root cause: an unchecked/misjudged boolean return from a token transfer allows internal accounting state (there: StableVault deposit/mint; here: `_orders[commitment][token]` escrow ledger) to advance as if the transfer succeeded when it did not.

By contrast, the input side of the same intent flow (escrowing on `placeOrder`) consistently uses `SafeERC20.safeTransferFrom`, which reverts on a `false` return: [4](#0-3) 

So the escrow-crediting path is safe, but the escrow-releasing path (`withdraw`) is not — an asymmetry that lets funds go in safely but get silently "written off" on the way out.

### Impact Explanation
Any input token used in an order (chosen by the order creator at `placeOrder` time) that can return `false` from `transfer` instead of reverting — e.g., a legacy/non-standard ERC20, a token with an address-level restriction (blacklist/pause) that fails silently for a particular beneficiary, or simply a balance shortfall in the gateway from any other rounding/dust condition — will cause `withdraw()` to permanently zero out `_orders[commitment][token]` and emit `EscrowReleased`/`EscrowRefunded` while the actual ERC20 balance stays locked in the `IntentGatewayV2` contract. The rightful beneficiary (solver or order creator) loses their entitled funds with no path to re-claim them, since the escrow slot has already been marked spent (`if (_orders[body.commitment][token] == 0) revert UnknownOrder();` guards re-entry to this same code path). This is a direct loss-of-funds / wrong-beneficiary-amount outcome as required by the impact gate — it does not require a malicious relayer, prover, or governance actor; the incoming `RedeemEscrow` message itself is fully authenticated via `authenticate(incoming.request)`.

### Likelihood Explanation
Likelihood is moderate to high: it doesn't require an attacker-controlled token to be "malicious" in an adversarial sense — it only requires any ERC20 implementation on the supported chain that follows the (spec-legal) "return false, don't revert" failure convention, or any beneficiary address subject to a token-side restriction (e.g., blacklisting, pausing) at settlement time. Given intent gateways are designed to be permissionless with respect to which tokens users escrow, this is a realistic, self-triggerable condition rather than a contrived edge case.

### Recommendation
Replace every raw `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` and the `SweepDust`/fee-redemption branches with `SafeERC20.safeTransfer`, consistent with the input-side `safeTransferFrom` usage already present in `placeOrder`. This ensures a `false` return value reverts the transaction instead of allowing escrow state to be debited for a transfer that never happened.

### Proof of Concept
1. Order creator calls `placeOrder` with an ERC20 input token `T` whose `transfer(to, amount)` implementation returns `false` (rather than reverting) when, e.g., `to` is blacklisted or contract balance momentarily insufficient — this is legal per the ERC20 interface and several deployed tokens behave this way.
2. A solver fills the order same-chain/cross-chain; Hyperbridge delivers the authenticated `RedeemEscrow` request to `onAccept` → `withdraw(body, false)`.
3. Inside `withdraw`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` executes without reverting but returns encoded `false`.
4. `success` is `true` (the call didn't revert), so `if (!success) revert TransferFailed();` does not fire.
5. `_orders[body.commitment][token] -= amount;` executes, zeroing the escrow slot, and `EscrowReleased` is emitted — but `beneficiary`'s token balance never increased; the tokens remain stuck in the `IntentGatewayV2` contract with no remaining accounting path to recover them for that commitment.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L392-400)
```text
                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-671)
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
