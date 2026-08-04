### Title
Fill-side escrow release trusts nominal output amounts, ignoring fee-on-transfer/deflationary token losses to the beneficiary - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol, evm/src/apps/intentsv2/ExtrinsicIntents.sol)

### Summary
`IntentGatewayV2`'s fill paths (`_fillSameChain` and `_fillCrossChain`) release the full nominal escrowed input to the solver based on the order's stated output amount, without ever checking that the beneficiary actually *received* that amount. If the output token has any transfer-time value loss (fee-on-transfer / deflationary mechanics), the beneficiary receives less than `totalRequired` while the solver still collects the entire escrowed input — exactly the Pendle-report pattern of trusting a nominal/quoted value (`SY.exchangeRate()` / `minTokenOut=0`) instead of the actual value realized on transfer.

### Finding Description
On `placeOrder`, the gateway is careful to measure actual received balances for input tokens and correct the escrow/commitment accordingly — see the non-predispatch branch: [1](#0-0) 

However, on the **fill** side, both same-chain and cross-chain paths compute the amount to release from escrow using the order's nominal `totalRequired`/`fillAmount` values and perform an unchecked `safeTransferFrom` to the beneficiary, never verifying the beneficiary's actual balance delta: [2](#0-1) 

The same pattern repeats in the cross-chain fill: [3](#0-2) 

In both cases, `escrowedAmount`/`outputFills[i]` (and thus the amount released to the solver via `_withdraw`/`WithdrawalRequest`) is derived purely from `order.output.assets[i].amount` and `fillAmount`, not from any post-transfer balance check on the beneficiary. `withdraw()` then unconditionally moves the full escrowed input to the solver: [4](#0-3) 

If the order's chosen output token has a transfer fee, rebasing behavior, or any deflationary transfer mechanic, `safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal)` delivers less than `beneficiaryTotal` to the beneficiary, yet the solver is still credited with the full nominal nominal `fillAmount`/`totalRequired` worth of escrowed input — an unconditional, unprivileged value mismatch that any solver can trigger by filling an order whose output token has such a mechanic.

### Impact Explanation
This produces "wrong beneficiary or amount" / "loss of funds": the beneficiary systematically receives less value than the order guarantees while the solver is paid the full nominal escrow, effectively letting an unprivileged solver skim the fee-on-transfer loss for themselves at the beneficiary's expense, on every fill of such orders. No relayer, prover, or admin collusion is required — it's exploitable by any solver calling the public `fillOrder` entrypoint.

### Likelihood Explanation
Likelihood depends on whether the deployment allowlists exotic tokens as valid intent output assets, but the gateway does not enforce any restriction against fee-on-transfer/deflationary ERC-20s for `order.output.assets`. Given that `placeOrder` explicitly hardens against exactly this class of token on the input side (see the fee-on-transfer tests in `IntentGatewayV2SameChainTest.sol`), the absence of symmetric protection on the output side is a real gap rather than a hardened, reviewed non-issue.

### Recommendation
Mirror the input-side fee-on-transfer handling on the output/fill side: measure the beneficiary's actual balance delta after each output transfer and either (a) release escrow proportional to the amount actually delivered, or (b) revert the fill if actual delivery is below `totalRequired`, consistent with the "all-or-nothing" cross-chain fill semantics already documented.

### Proof of Concept
1. User places a same-chain order with `output.assets[0].token` = a fee-on-transfer ERC20 (e.g., 1% transfer fee) and `amount = 1000`.
2. Solver calls `fillOrder` with `options.outputs[0].amount = 1000`.
3. `_fillSameChain` executes `IERC20(token).safeTransferFrom(solver, beneficiary, 1000)`; due to the token's fee, beneficiary's balance only increases by 990.
4. `amountFilled` is recorded as `1000` (nominal), `isFullyFilled = true`, and `escrowedAmount = _orders[commitment][inputToken]` (the full escrow) is released to the solver via `_withdraw`.
5. Net result: beneficiary is short 10 tokens of the agreed output while the solver receives 100% of the escrowed input — repeatable on every such fill, analogous to `FeeOnTransferToken` already used in `evm/tests/foundry/IntentGatewayV2SameChainTest.sol` (lines 2501-2547) but exercised here on the *output* leg where no compensating balance check exists.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L281-297)
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
```

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L98-134)
```text
        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            if (solverAmount < totalRequired) revert InvalidInput();

            uint256 dust = solverAmount - totalRequired;
            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;

            if (dust > 0) {
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            }

            if (token == address(0)) {
                if (msgValue < solverAmount) revert InsufficientNativeToken();
                uint256 beneficiaryTotal = totalRequired + beneficiaryShare;
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, totalRequired + beneficiaryShare);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }
            if (protocolShare > 0) emit DustCollected(token, protocolShare);
            outputFills[i] = TokenInfo({token: outputToken, amount: totalRequired});
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L391-410)
```text
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
