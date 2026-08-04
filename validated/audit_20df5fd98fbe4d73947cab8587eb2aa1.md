### Title
Escrow crediting in Tron `IntentGatewayV2.placeOrder` uses the declared input amount instead of the actual tokens received, causing shortfall/insolvency for non-standard ERC20s - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`placeOrder` in the Tron variant of `IntentGatewayV2` computes the protocol-fee-reduced escrow amount from the **user-declared** `order.inputs[i].amount` and credits `_orders[commitment][token]` with that value, without ever measuring the token balance the contract actually received via `safeTransferFrom`. This is the same class of bug as the Synthetix M-36 report: the code books an amount that was *supposed* to arrive rather than the amount that *actually* arrived, so bookkeeping can promise more tokens than the contract physically holds.

### Finding Description
In the non-predispatch path: [1](#0-0) 

```solidity
} else {
    for (uint256 i; i < inputsLen;) {
        if (order.inputs[i].amount == 0) revert InvalidInput();
        address token = address(uint160(uint256(order.inputs[i].token)));
        if (token == address(0)) {
            if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
            msgValue -= order.inputs[i].amount;
        } else {
            IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
        }
        // Store reduced amount (after protocol fees) in escrow
        _orders[commitment][token] += reducedInputs[i].amount;
        ...
```

`reducedInputs[i].amount` is derived purely from `order.inputs[i].amount` (the caller-declared value) minus a protocol fee percentage computed on that same declared value: [2](#0-1) 

No balance-before/balance-after check is performed. For any ERC20 with a transfer fee, rebase, or other non-1:1 transfer semantics, `IERC20(token).safeTransferFrom` deposits *less* than `order.inputs[i].amount` into the contract, yet the escrow ledger (`_orders[commitment][token]`) is still credited as if the full declared amount arrived. The same defect exists in the predispatch branch, which credits `reducedInputs[i].amount` regardless of the dust/shortfall computed from the dispatcher sweep: [3](#0-2) 

This is precisely the bug pattern flagged in the external report: a downstream value (margin/escrow) is derived from the *pre-transfer* input amount instead of the *post-transfer* actually-received amount.

By contrast, the mainline EVM `IntentGatewayV2.sol` was hardened against this exact issue: it snapshots `balanceOf` before and after every transfer/sweep and mutates `order.inputs[i].amount` to the measured `received` value before computing `reducedInputs` and the commitment/escrow: [4](#0-3) 

The Tron contract lacks this "actual received" measurement entirely, so it regressed to the unsafe pattern.

### Impact Explanation
Because `_orders[commitment][token]` can be credited with more than the token balance the contract actually holds for that order, `withdraw()` (called from `onAccept` for `RedeemEscrow`/`RefundEscrow`, or directly for same-chain cancellation) will pay out the inflated `reducedInputs[i].amount`: [5](#0-4) 

If the gateway's on-chain balance of that token is a shared pool across multiple concurrently open orders, an over-credited entry lets its beneficiary (user on cancel/refund, or solver on fill/redeem) drain tokens that belong to other orders' escrow, since `_orders[...]` is just a bookkeeping mapping, not a segregated balance. This is fund loss / wrong-beneficiary payout for other order holders and, in the worst case (last redeemers with insufficient contract balance), a hard revert / denial of service that locks their legitimate withdrawal.

### Likelihood Explanation
Any unprivileged user can place an order using a fee-on-transfer, deflationary, or rebasing ERC20 as an input token — no privileged role, relayer, or malicious peer is required. Given `placeOrder` is a fully public entry point that accepts an arbitrary caller-supplied token address (`order.inputs[i].token`), this is directly triggerable and requires no cooperation from any other party.

### Recommendation
Mirror the mainline EVM `IntentGatewayV2.sol` fix in the Tron contract: measure `IERC20(token).balanceOf(address(this))` before and after each `safeTransferFrom`/dispatcher sweep, use the measured `received` amount (not the declared `order.inputs[i].amount`) as the basis for the protocol-fee calculation, the commitment hash, and the value credited to `_orders[commitment][token]`.

### Proof of Concept
1. Deploy a 1%-fee-on-transfer ERC20 and register it as a valid input token.
2. User A calls `placeOrder` with `inputs[0].amount = 1000` of the FOT token; `safeTransferFrom` moves only 990 tokens into the gateway, but `_orders[commitmentA][token]` is credited with `reducedInputs[0].amount` derived from 1000 (e.g. 1000 minus protocol fee, ~995-1000 depending on fee bps) — an amount larger than the 990 tokens actually held for this order.
3. User B places a second order for the same token, contributing another 990 actual tokens; total contract balance for the token is now 1980, while the sum of `_orders[...]` entries for A and B together can exceed 1980 (e.g. ~1990+).
4. When both orders are filled/redeemed via `withdraw()`, the first redeemer receives their full over-credited amount, leaving insufficient balance for the second redeemer — causing either theft of B's share by A or a revert (DOS) for B's legitimate withdrawal.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L353-368)
```text
        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L410-440)
```text
            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-699)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L198-298)
```text
        // Phase 1: Transfer tokens and record actual received amounts.
        // For fee-on-transfer tokens, the gateway receives less than the requested amount.
        // We mutate order.inputs to reflect actual received so the commitment and escrow
        // are consistent with what the gateway holds.
        uint256 msgValue = msg.value;
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;
                if (amount == 0) revert InvalidInput();

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }

            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));

            // Measure actual received, emit dust for excess, update order.inputs.
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 received;
                if (token == address(0)) {
                    received = address(this).balance - balancesBefore[i];
                } else {
                    received = IERC20(token).balanceOf(address(this)) - balancesBefore[i];
                }

                if (received > order.inputs[i].amount) {
                    uint256 dust = received - order.inputs[i].amount;
                    emit DustCollected(token, dust);
                } else {
                    order.inputs[i].amount = received;
                }

                unchecked {
                    ++i;
                }
            }
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
