### Title
Unchecked boolean return value on outbound ERC-20 transfers in `IntentGatewayV2::withdraw()` permanently locks escrowed funds - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
The Tron `IntentGatewayV2` contract uses OpenZeppelin's `SafeERC20.safeTransferFrom` for *inbound* token transfers (deposits/escrow funding), which correctly decodes and reverts on a `false` return value. However, for *outbound* transfers that release escrowed funds — `withdraw()` and the dust-sweep path — the contract reverts to a raw low-level `.call` and only checks that the call did not revert, without decoding the ABI-encoded boolean return value, reproducing exactly the "no-revert-on-failure" ERC-20 hazard cited in the seed report.

### Finding Description
In `withdraw()`, escrow release is implemented as: [1](#0-0) 

```solidity
if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    if (!sent) revert InsufficientNativeToken();
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    if (!success) revert TransferFailed();
}
```

The code checks only that the low-level `call` did not revert (`success`), but never inspects the returned bytes to confirm the ERC-20 `transfer()` actually returned `true`. This is the identical unchecked-return-value pattern the external report flags in `TransferHelper::_safeTransferFrom` — a token whose `transfer()` returns `false` on failure instead of reverting (the same "no-revert-on-failure" class cited from `weird-erc20`) will make `success == true` while the transfer silently did nothing.

Immediately after this unguarded transfer, the function unconditionally advances protocol state as if the transfer succeeded: [2](#0-1) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;
    ...
        _orders[body.commitment][token] -= amount;
```

`_filled[body.commitment]` is set and `_orders[body.commitment][token]` is decremented (in this case, to zero, since the full escrowed amount is being withdrawn) regardless of whether tokens were actually delivered. The same fee-redemption block further down uses the identical unchecked pattern for `feeToken`, and the dust-sweep loop earlier in the file (lines 661-673) has the same defect for `DustSwept`.

By contrast, the same file's inbound escrow funding correctly uses the return-value-checking `SafeERC20.safeTransferFrom`: [3](#0-2) 

confirming the outbound path is an inconsistent, unguarded regression relative to the rest of the contract.

### Impact Explanation
Because `_orders[body.commitment][token]` is set to zero and `_filled[body.commitment]` is marked before/without verifying actual token delivery, a failed-but-non-reverting `transfer()` results in: the escrow accounting believing the order is fully settled, `EscrowReleased`/`EscrowRefunded` being emitted, and no path to retry or reclaim the funds for that beneficiary — `UnknownOrder` will now trigger on any subsequent withdrawal attempt since the escrowed balance was already zeroed. This is a permanent loss of the beneficiary's escrowed principal held by the `IntentGatewayV2` contract: real value moves nowhere (it stays trapped in the contract), while the protocol's internal bookkeeping asserts the withdrawal succeeded — a direct instance of "loss of funds" / false state acceptance on a fund-custody path.

### Likelihood Explanation
This is reachable through the normal, unprivileged cross-chain settlement flow: any order whose input token is a non-reverting-on-failure ERC-20 (a documented and non-exotic class of tokens, per `weird-erc20`) will trigger this path the moment `transfer()` returns `false` for any reason (e.g., a paused token, a blacklisted beneficiary, or any custom failure condition the token defines). No malicious relayer, prover, or governance actor is required — the order creator/solver only needs to use such a token as collateral, and the failure condition can be triggered by ordinary token-level restrictions rather than by compromising any bridge participant.

### Recommendation
Replace the raw `token.call(...)` outbound transfers in `withdraw()` (and the dust-sweep loop) with OpenZeppelin's `SafeERC20.safeTransfer`, matching the pattern already used for inbound transfers in this same file, so a `false` return value reverts the transaction instead of allowing escrow state to advance past a transfer that never happened.

### Proof of Concept
1. An order is created on the source chain with `order.inputs[i].token` set to a non-standard ERC-20 that returns `false` (rather than reverting) when `transfer()` fails for a given recipient/condition (e.g., a token that returns `false` when the sender is temporarily paused or blacklisted, or a mock malicious token deployed by the order creator for their own order).
2. The order is filled on the destination chain, and a valid `RedeemEscrow` request is relayed back and delivered to `onAccept`, invoking `withdraw()`.
3. Inside `withdraw()`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` executes; the token's internal logic returns `false` without reverting (e.g., because the configured condition is active), so `success == true`.
4. `if (!success) revert TransferFailed();` does not trigger. `_filled[body.commitment]` is set and `_orders[body.commitment][token] -= amount` zeroes the escrow record, while `beneficiary` received zero tokens.
5. Any later attempt to redeem the same commitment now reverts with `UnknownOrder` (escrow already zero), permanently locking the originally escrowed tokens inside the `IntentGatewayV2` contract with no recovery path.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L452-454)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
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
