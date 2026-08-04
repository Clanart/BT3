Based on my investigation, I found a genuine local analog: a stale/reset state-ordering bug in the same-chain intent fill path, structurally similar to the "timestamp not reset when it should be" pattern — here, escrow state (`_orders[commitment][token]`) is read *after* an untrusted external call instead of before it, allowing the escrow to be drained through a reentrant call before the reader observes the correct value.

### Title
Reentrant cancellation during native-token fill lets an order's own beneficiary drain solver-owed escrow - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`_fillSameChain` sends native ETH to an attacker-controlled `beneficiary` mid-loop (line 104) and only *afterwards* reads `_orders[commitment][token]` to compute how much escrow to release to the solver (lines 116-121). Because `order.user` and `order.output.beneficiary` are both freely chosen by the order creator, they can be set to the same malicious contract, whose `receive()` callback reenters `cancelOrder` → `_cancelSameChain` → `_withdraw(..., finalize=true)` while the outer fill is still executing.

### Finding Description [1](#0-0) 
shows that per-iteration, `_partialFills` is updated (line 98), then a native transfer is made directly to `beneficiary` (line 104), and only after that call returns is `escrowedAmount` computed by reading the live `_orders[commitment][token]` value (lines 116-121).

`_cancelSameChain` performs no reentrancy check against an in-flight fill: [2](#0-1) 
It only verifies `order.user == msg.sender`, then reads the *current* `_orders[commitment][token]` balances and calls `_withdraw(body, true, true)`, which zeroes those balances and pays them straight to `order.user`.

`_withdraw` itself is order-agnostic about who called it and silently skips zero-amount tokens instead of reverting: [3](#0-2) 

Attack sequence:
1. Attacker places an order with `order.user = order.output.beneficiary = AttackerContract`, output token = native ETH.
2. A solver calls `fillOrder`, which sets `_filled[commitment] = solver` (line 57) and enters the loop.
3. At line 104, ETH is sent to `AttackerContract.receive()`, which reenters and calls `cancelOrder(order)` before the outer loop finishes.
4. `_cancelSameChain` sees full, un-decremented escrow still in `_orders[commitment][token]` (the outer `_withdraw` for the fill hasn't run yet — that only happens once, after the whole loop, at line 134), refunds 100% of it to the attacker, and sets `_filled[commitment] = attacker`.
5. Control returns to the outer loop; `escrowedAmount` is now read as `0` because `_orders[commitment][token]` was just zeroed by the reentrant cancel.
6. The outer `_withdraw(body, false, isFullyFilled)` call then sees `amount == 0` and silently `continue`s instead of reverting, so the fill completes and even overwrites `_filled[commitment]` back to the solver, emitting a normal `OrderFilled` event.

Net effect: the solver paid the full output amount to the attacker's beneficiary contract, but the escrowed input tokens that were supposed to compensate the solver were instead redirected to the attacker via the "cancel" path — the solver receives nothing.

### Impact Explanation
This is direct loss of funds for the solver and unauthorized redirection of escrowed assets to the wrong beneficiary, matching the bounty's "stealing or loss of funds," "wrong beneficiary or amount," and "logic attacks" categories. No relayer, prover, admin, or governance actor is involved — the attacker is simply the order's own (unprivileged) creator using a malicious beneficiary contract, which is a fully permitted, ordinary user action.

### Likelihood Explanation
Requires only: (1) placing an order with a malicious contract as both `user` and native-ETH `output.beneficiary`, and (2) a solver choosing to fill that order with native ETH output. No special timing, front-running, or privileged role is needed — the reentrancy trigger is the unavoidable native-token transfer inside the fill itself.

### Recommendation
Follow checks-effects-interactions: read/reserve `_orders[commitment][token]` amounts and finalize/settle escrow bookkeeping for the *entire* fill before making any external token transfers to `beneficiary`, and/or add a reentrancy guard shared across `fillOrder`/`cancelOrder` so a fill in progress cannot be interleaved with a cancellation on the same commitment.

### Proof of Concept
Deploy a malicious `AttackerBeneficiary` contract that is both `order.user` and `order.output.beneficiary` for a same-chain order with a native-ETH output. Have `receive()` call back into the `IntentGateway`'s `cancelOrder(order)`. Have a solver call `fillOrder` with sufficient ETH. Assert: solver's `usdc`/input-token balance does not increase by the expected `escrowedAmount`, while the attacker contract receives both the solver's ETH output and the full input escrow refund via `EscrowRefunded`.

**Caveat**: the repository already contains a dedicated `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol` file covering `fillOrder`/`cancelOrder`/`_filled`/`_cancelSameChain`/`_fillSameChain` interactions, which I was not able to open/read before running out of tool budget. It's possible this exact scenario is already covered by an existing guard (e.g., a reentrancy modifier not visible in the snippets I reviewed) that neutralizes the path described above — this should be verified against that test file before treating the finding as unpatched. [4](#0-3)

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L97-123)
```text
            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;

            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }

            if (totalRequired > amountFilled) isFullyFilled = false;
            if (protocolShare > 0) emit DustCollected(token, protocolShare);

            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-187)
```text
    function _cancelSameChain(Order calldata order, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        if (orderSource != currentChain) revert WrongChain();

        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
        if (!hasEscrow) revert UnknownOrder();

        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L1-1)
```text
// Copyright (C) Polytope Labs Ltd.
```
