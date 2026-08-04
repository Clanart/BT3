## Title
Unchecked ERC20 boolean-return on escrow payout lets a non-reverting token silently fail while `IntentGatewayV2` marks the order filled/refunded — permanent loss of escrowed funds - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2`'s `withdraw()` function (Tron variant), reached via `onAccept` for `RedeemEscrow`/`RefundEscrow` requests, pays out escrowed tokens using a raw low-level `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and only checks that the **call itself did not revert** (`success`). It never decodes/checks the returned `bool` payload. This is the exact bug class from the external report ("not checking the ERC20 transfer result"), just on the payout/redemption leg instead of the deposit leg. If the escrowed token is a non-reverting ERC20 that returns `false` on failure instead of reverting, the withdrawal is treated as successful — escrow accounting is decremented, the order is marked filled/refunded, and events are emitted — even though the beneficiary received nothing.

### Finding Description
`withdraw()` iterates the escrowed tokens and pays the beneficiary: [1](#0-0) 

and pays out accumulated transaction fees the same way: [2](#0-1) 

Both call sites use `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` and gate solely on `success` (whether the low-level call reverted), never inspecting the returned ABI-encoded boolean. Per EIP-20, a compliant transfer may legally return `false` on failure without reverting; several real tokens exhibit exactly this behavior (e.g., under pause, blacklist, or balance-exhaustion conditions implemented as "return false" rather than "revert"). In that scenario, `success == true` (the external call executed without reverting) while the actual token balance movement never happened.

Immediately after the unchecked "success" branch, the function commits state that cannot be undone or retried:
- `_orders[body.commitment][token] -= amount;` — escrow accounting is decremented as if funds left the contract.
- `_filled[body.commitment] = beneficiary;` is set unconditionally at function entry.
- `EscrowReleased`/`EscrowRefunded` is emitted, signaling successful settlement to indexers/relayers/UIs.

This is reached through the cross-chain `RedeemEscrow`/`RefundEscrow` flow: [3](#0-2) 

`authenticate(incoming.request)` only verifies the message originates from the legitimate paired IntentGateway instance on the counterpart chain — it says nothing about the ERC20 token used inside the order, which is chosen by whoever created the order on the source chain. So an order that legitimately deposited (via `safeTransferFrom`, which is correct) can still fail on the payout leg if the input/output token is a non-reverting ERC20, since the payout path does not use `safeTransfer`.

The same unchecked pattern also appears in the governance-triggered dust-sweep path: [4](#0-3) 

By contrast, the deposit paths in the same contract correctly use `safeTransferFrom`: [5](#0-4) [6](#0-5) [7](#0-6) 

confirming the codebase already knows to use `SafeERC20` and imports it (`using SafeERC20 for IERC20;`), but the payout/redeem path was not updated to use `safeTransfer`.

### Impact Explanation
This directly matches the bounty's "stealing or loss of funds" and "false state acceptance" categories: escrow state is marked settled (`_filled`, `_orders` decremented, `EscrowReleased`/`EscrowRefunded` emitted) while the beneficiary never receives the underlying asset. Because `_filled[body.commitment]` is set and `_orders[commitment][token]` is decremented unconditionally, there is no retry mechanism — the funds become permanently stuck in the `IntentGatewayV2` contract with no code path left to redeem them for that commitment. This is a genuine bridge-custody fund-loss bug in the settlement/redemption leg, not a dependency or peer-trust issue.

### Likelihood Explanation
The path is reachable by any unprivileged order creator/filler who selects (or is tricked into using) an ERC20 token with non-reverting failure semantics as an order's input/output token — token selection is order-creator controlled, not an admin/relayer/prover privilege. No malicious relayer, prover, or governance actor is required; the relayer/prover only needs to deliver a legitimately authenticated `RedeemEscrow`/`RefundEscrow` message, which is the normal, expected flow. The trigger condition (a token contract returning `false` instead of reverting under some failure mode — e.g., pausable/blacklistable stablecoins) is a known, non-exotic ERC20 pattern, making this a realistic scenario rather than a purely theoretical one.

### Recommendation
Replace the raw `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` + `success`-only check in `withdraw()` (both the token loop and the fee payout) and in the `SweepDust` handler with `SafeERC20.safeTransfer(beneficiary, amount)`, consistent with the deposit-side `safeTransferFrom` usage already present in this contract. This ensures a `false` return value reverts the transaction instead of silently marking escrow as settled.

### Proof of Concept
1. Create/settle an order whose input token is a compliant-but-non-reverting ERC20 that returns `false` on transfer failure (e.g., simulate a pausable token that is paused between deposit and redemption, or a token whose `transfer` returns `false` when the internal balance check fails instead of reverting).
2. Deposit succeeds normally via `safeTransferFrom` in the order-creation path (lines 398-400 / 452-454 / 478-479), so `_orders[commitment][token]` correctly reflects escrowed funds.
3. Trigger token failure condition (e.g., pause the token, or arrange for the contract's balance to be insufficient at the moment of payout due to a race with the dust-sweep/fee path).
4. Relayer delivers the legitimate `RedeemEscrow` (or `RefundEscrow`) message; `onAccept` → `authenticate` succeeds (source is the correct paired gateway) → `withdraw()` executes.
5. `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` returns `(success=true, data=abi.encode(false))` — the call did not revert, only the boolean payload indicates failure.
6. `withdraw()` does not decode `data`, so it proceeds: `_orders[commitment][token] -= amount`, `_filled[commitment] = beneficiary`, and emits `EscrowReleased`/`EscrowRefunded`.
7. Beneficiary's on-chain token balance is unchanged — the escrowed funds remain trapped in the contract with no remaining code path referencing that commitment to retry the transfer.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L398-400)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L452-454)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L478-479)
```text
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L693-705)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L708-714)
```text
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```
