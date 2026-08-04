## Analysis

The stETH report's core broken invariant: **the contract assumes `transferFrom(amount)` always delivers exactly `amount`, and credits internal accounting with the requested amount instead of the amount actually received.** When it doesn't (fee-on-transfer, rebasing/rounding tokens), later transfers based on the inflated bookkeeping revert or drain shared balance, causing lock/loss of funds for other users.

The main EVM `IntentGatewayV2.sol` (`evm/src/apps/IntentGatewayV2.sol`) already defends against this — it snapshots balances before/after `safeTransferFrom` and mutates `order.inputs[i].amount` to the actual delta received, as proven by the fee-on-transfer test suite in `evm/tests/foundry/IntentGatewayV2SameChainTest.sol`. The **Tron** variant of the same contract does not carry this fix.

### Title
Tron IntentGatewayV2.placeOrder credits escrow with the requested input amount instead of the amount actually received, letting non-standard ERC20s (fee-on-transfer, rebasing/rounding tokens) overstate escrow and lock or drain other users' funds - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, the non-predispatch branch of `placeOrder` performs `IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount)` and then unconditionally increments `_orders[commitment][token] += reducedInputs[i].amount`, where `reducedInputs[i].amount` is derived purely from the user-specified `order.inputs[i].amount` minus the protocol fee — never from the gateway's actual token balance delta. [1](#0-0) 

### Finding Description
For any ERC20 whose `transfer`/`transferFrom` can deliver less than the nominal amount requested (fee-on-transfer tokens, or share-based/rounding tokens such as stETH), the gateway will hold strictly less than what it credits to `_orders[commitment][token]`. This is the exact same failure mode described in the stETH report: the contract "assumes it always receives the exact amount ... specified by the user."

Compare with the corresponding logic in the primary EVM contract, which explicitly measures the actual received amount via a balance snapshot before crediting escrow or computing the commitment: [2](#0-1) 

The Tron contract has no equivalent balance check in its non-predispatch escrow path — it trusts `order.inputs[i].amount` as ground truth for both the transfer and the credited escrow. The predispatch branch is partially instrumented (it computes `dust` from the dispatcher's balance) but even there the amount credited to escrow, `reducedInputs[i].amount`, is still derived from the user-declared amount rather than the true balance actually pulled from `msg.sender`, so the discrepancy is only partially caught.

Downstream, `withdraw()` in the same file transfers `body.tokens[i].amount` — sourced from `_orders[commitment][token]` — directly out of the contract's balance: [3](#0-2) 

Because `_orders[...]` can be inflated relative to the gateway's real token balance, any of the following can happen:
- A `withdraw()` call (triggered by `onAccept` for `RedeemEscrow`/`RefundEscrow`, or `onGetResponse` for cancellation) reverts with `TransferFailed()` because the contract lacks the credited balance — a denial of service for that order and, since token balances are shared across all commitments for that token, potentially for other orders too.
- Worse, because multiple orders share the same ERC20 contract balance, an order that is processed first can drain the actual token balance, leaving a later, legitimately-escrowed order with nothing to redeem/refund on withdrawal — an outright loss of funds for that user, not merely a revert.

### Impact Explanation
This breaks bridge custody/intent-settlement invariants: escrowed input tokens must move exactly once, to the rightful beneficiary, in the exact amount actually held. An inflated internal ledger relative to real token custody causes fund lock or loss for depositors/solvers using non-standard ERC20 tokens (fee-on-transfer or share/rounding tokens) on the Tron deployment of IntentGatewayV2, with no privileged actor, admin, relayer, or malicious peer required — a normal user placing an order with such a token triggers it.

### Likelihood Explanation
Any ordinary user can trigger the issue simply by using a fee-on-transfer or rebasing/rounding ERC20 as the order's input asset — this requires no relayer, prover, or governance compromise, matching the unprivileged-entrypoint requirement. Its likelihood depends on whether such tokens are permitted as intent inputs on the Tron deployment; if they are (as the fix in the main EVM contract, plus its dedicated `FeeOnTransferToken` test suite, implies they are expected to be supported), the analog is directly exploitable.

### Recommendation
Mirror the balance-snapshot pattern already implemented in `evm/src/apps/IntentGatewayV2.sol`: measure the gateway's actual token balance before and after each `safeTransferFrom` in `evm/tron/contracts/apps/IntentGatewayV2.sol::placeOrder`, use that delta (not `order.inputs[i].amount`) to compute `reducedInputs` and to credit `_orders[commitment][token]`, and apply the same treatment to the predispatch branch's transfer from `msg.sender` to the dispatcher.

### Proof of Concept
1. Deploy a 1% fee-on-transfer ERC20 (as in `evm/tests/foundry/IntentGatewayV2SameChainTest.sol`'s `FeeOnTransferToken`) and register it as an order input on the Tron `IntentGatewayV2`.
2. User calls `placeOrder` with `inputs[0].amount = 1000e18`. `safeTransferFrom` delivers only `990e18` to the gateway, but `_orders[commitment][token]` is credited `reducedInputs[0].amount ≈ 1000e18` (minus protocol fee only).
3. A second, unrelated user places another order using the same token, again under-delivering to the gateway due to the transfer fee.
4. When the first order is filled/cancelled and `withdraw()` attempts to pay out the full credited escrow, the gateway's real balance of the token is insufficient (drained by the discrepancy across both orders), and the `token.call(transfer...)` returns `false`, reverting with `TransferFailed()` — or, if the first withdrawal succeeds by draining the shared pool, the second user's legitimate withdrawal reverts/loses funds. [4](#0-3)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-463)
```text
        } else {
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

                unchecked {
                    ++i;
                }
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

**File:** evm/src/apps/IntentGatewayV2.sol (L281-298)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
        }
```
