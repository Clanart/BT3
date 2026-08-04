### Title
Escrow payout in `withdraw()` accepts a no-code token as a successful transfer, permanently losing funds to solvers/users - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` on the Tron variant of the Intent Gateway releases escrowed ERC-20 input tokens with a raw low-level `.call()` that only checks the boolean `success` flag, instead of using the already-imported `SafeERC20` library used everywhere else in the same file. A `.call()` made to an address with no deployed bytecode always returns `success == true` with empty return data on the EVM/TVM. This is the exact bug class from the external report (a `safeTransferFrom`/`safeTransfer` wrapper that treats a no-code address as a valid ERC-20), but relocated from the deposit side to the **payout** side of an escrow, where it causes silent, permanent loss of the beneficiary's funds while the contract's internal accounting proceeds as if the transfer succeeded.

### Finding Description
`placeOrder()` in this file correctly escrows tokens using OpenZeppelin's modern `SafeERC20.safeTransferFrom` (`using SafeERC20 for IERC20;` at line 56), which reverts if the target address has no code: [1](#0-0) 

However, when escrow is later released — either to the solver on `RedeemEscrow`/cross-chain fill settlement, or to the user on `RefundEscrow`/cancellation — `withdraw()` does **not** use `SafeERC20.safeTransfer`. It instead performs a raw low-level call and checks only `success`: [2](#0-1) 

The same unsafe pattern is repeated in the `SweepDust` handler inside `onAccept`: [3](#0-2) 

Because a `.call()` to an address with zero bytecode always returns `success = true` and `data.length == 0`, `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` cannot distinguish "the ERC-20 contract successfully transferred tokens" from "the address has no code and nothing happened." Unlike the modern `SafeERC20._callOptionalReturn` used at escrow time (which explicitly reverts when `returnSize == 0 && address(token).code.length == 0`), this manual call has no such guard.

The escrow-time check (`safeTransferFrom`) does guarantee the token has code *at the moment of escrow*. The exploitable gap is the time-of-check/time-of-use window between `placeOrder()` and `withdraw()`: an order placer can escrow a self-destructible malicious ERC-20 they control, wait for a solver to fill the order with real value, then destroy the malicious token's bytecode (removing it from the deployed address) before the settlement/`withdraw()` message is delivered. When `withdraw()` executes, the no-code `.call()` trivially "succeeds," `_orders[commitment][token] -= amount` accounting proceeds as if the solver/user was paid, and `EscrowReleased`/`EscrowRefunded` fires — but zero tokens actually move. This is specifically relevant on Tron (hence this file's separate, non-standard implementation), because TVM still honors classic `SELFDESTRUCT` semantics that fully remove contract code, unlike post-Cancun EVM chains where `SELFDESTRUCT` no longer deletes code outside the deploying transaction.

### Impact Explanation
This is a direct loss-of-funds / false-settlement-acceptance bug: the intended beneficiary (a solver who already delivered real output tokens on a cross-chain fill, or a user cancelling an order) never receives their escrowed asset, yet the protocol's on-chain state (`_orders` mapping, `_filled`, emitted events) records the settlement as complete. There is no way to retry or recover — the escrow accounting is already decremented. This matches the bounty's "stealing or loss of funds" and "transaction manipulation / false state acceptance" categories, and requires no relayer, prover, or admin compromise — only an unprivileged order placer deploying a self-destructing token contract of their own choosing.

### Likelihood Explanation
Likelihood is moderate-to-high on Tron specifically: an attacker fully controls the malicious ERC-20 contract used as the order's input token (this is standard in intent-based systems — solvers/relayers generally trust that any token accepted for escrow behaves like a normal ERC-20 at time of accounting, but nothing prevents the attacker from picking their own token and later destroying it). The only timing requirement is that the token be self-destructed after `placeOrder()`'s `safeTransferFrom` succeeds and before `withdraw()` executes — which is naturally available on cross-chain orders due to the relay/proof delay between fill and settlement, and even on same-chain cancels if the attacker times the self-destruct within the same block/transaction sequence they control.

### Recommendation
Replace the manual `token.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` patterns in `withdraw()` and the `SweepDust` branch of `onAccept()` with `IERC20(token).safeTransfer(beneficiary, amount)`, using the `SafeERC20` library already imported and used elsewhere in this file. This restores the `code.length` check that guards against no-code/self-destructed addresses being treated as successful transfers, consistent with the already-fixed pattern used in `placeOrder()`.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC-20 with a normal `transferFrom`/`transfer` implementation and an additional public `kill()` function that calls `selfdestruct`.
2. Attacker calls `placeOrder()` on the Tron `IntentGatewayV2`, escrowing `1,000,000 EvilToken` as `order.inputs[0]`. `safeTransferFrom` succeeds because `EvilToken` has code (`evm/tron/contracts/apps/IntentGatewayV2.sol:397-400`).
3. An honest solver, seeing the order, calls `fillOrder`, delivering real value (e.g., USDC) to the attacker's beneficiary address, and the cross-chain `RedeemEscrow` request is dispatched back to the source chain.
4. Before the relayer delivers the proof and `onAccept`→`withdraw()` executes, the attacker calls `EvilToken.kill()`, self-destructing the contract and clearing its bytecode on TVM.
5. When `withdraw()` finally runs (`evm/tron/contracts/apps/IntentGatewayV2.sol:682-705`), `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` targets the now-code-less address, returns `success = true` trivially, and the function proceeds: `_orders[commitment][token] -= amount` and `EscrowReleased` is emitted.
6. The solver, who already paid real USDC to the attacker's beneficiary, receives **zero** `EvilToken` in return — a full loss of the solver's escrowed payout, with the contract state falsely recording the escrow as released.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L397-400)
```text
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-673)
```text
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

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
