## Finding

### Title
User-deposited relayer fee (`TRANSACTION_FEES` escrow) is permanently stuck in `IntentGatewayV2` because no fill or cancel path ever releases it — ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
`placeOrder` escrows an optional relayer fee (`order.fees`) under a dedicated storage slot keyed by the sentinel `TRANSACTION_FEES`, separate from the per-input escrow entries keyed by `order.inputs[i].token`. Every release path in the contract (same-chain fill, same-chain cancel, cross-chain fill) builds its token list to withdraw exclusively from `order.inputs`/`escrowedInputs`, never from the `TRANSACTION_FEES` slot. As a result, the fee token the user paid for relaying is credited into escrow but has no code path that ever debits it back out — it accumulates in the contract exactly like the ZetaChain `feeInZeta` residue that was minted but never retrievable from the `crosschain` module.

### Finding Description
In `placeOrder`, whenever `order.fees > 0` the contract pulls (or swaps for) the fee token and stores it under a fixed sentinel key: [1](#0-0) 

This entry lives in the same `_orders[commitment][token]` mapping used for ordinary input escrow, but under `TRANSACTION_FEES` instead of a real token address deducted from `order.inputs`.

Every subsequent release of escrow builds its withdrawal token list strictly from `order.inputs` / `escrowedInputs`, never touching `TRANSACTION_FEES`:

- Same-chain fill — `escrowedInputs` is sized and populated only from `order.inputs`: [2](#0-1) [3](#0-2) 

- Same-chain cancel — `remainingTokens` is built purely by iterating `order.inputs`: [4](#0-3) 

- Cross-chain fill — the `WithdrawalRequest` dispatched back to the source chain carries `order.inputs` as `tokens`, and the relayer fee actually used to pay the dispatch is a *separately supplied* `options.relayerFee` from the filler's own funds (`payer: msg.sender`), not the escrowed `order.fees`: [5](#0-4) 

- Cross-chain cancel (source-initiated / dest-initiated) similarly builds its `WithdrawalRequest.tokens` from `order.inputs` only: [6](#0-5) [7](#0-6) 

None of these four paths — the only ways escrow is ever released in this contract — include `TRANSACTION_FEES` in the token set passed to `_withdraw`. Since `_withdraw` operates over the token list it is handed, any balance recorded at `_orders[commitment][TRANSACTION_FEES]` is structurally unreachable: it is neither paid to a relayer, refunded to the user on cancellation, nor released to the solver. It just sits in the contract's balance, indistinguishable from (but not covered by) the governance `SweepDust`/`DustCollected` accounting, which only tracks amounts explicitly emitted via `DustCollected` (protocol fee deductions and surplus) — the `TRANSACTION_FEES` escrow is never emitted as dust, so it is invisible to the sweep-dust governance flow (`_sweepDust`) as well.

This mirrors the ZetaChain root cause precisely: an amount is deliberately escrowed/minted for a specific downstream purpose (paying for a swap/paying a relayer), the downstream consumption path never draws it down, and there is no retrieval function scoped to that specific liability.

### Impact Explanation
Any user who places an order with `order.fees > 0` permanently loses that fee amount — it is deducted from their wallet at `placeOrder` and never returned to them, never paid to the relayer it was meant to compensate, and never recoverable by them. This is a direct, unauthorized loss of user funds through ordinary use of the public `placeOrder`/`fillOrder`/`cancelOrder` entrypoints, requiring no privileged actor, malicious relayer, or governance action — satisfying the "loss of funds" bounty criterion.

### Likelihood Explanation
High. Any order created with a non-zero `order.fees` value (a first-class, documented field of the `Order` struct that the SDK and docs describe as the fee mechanism) hits this path on every single fill or cancel, with no special conditions required. It is a systemic accounting gap, not an edge case.

### Recommendation
Include the `TRANSACTION_FEES` slot balance in every release path:
- On cross-chain/same-chain fill, pay the escrowed `TRANSACTION_FEES` amount to whichever party is meant to be reimbursed for delivery (the filler/relayer who dispatches the redeem message), or fold it into `options.relayerFee` reconciliation.
- On any cancel/refund path, refund the escrowed `TRANSACTION_FEES` amount back to `order.user` alongside the input tokens.
- Add a regression test that asserts the gateway's fee-token balance returns to zero after a full fill/cancel lifecycle for an order with `order.fees > 0`.

### Proof of Concept
1. Call `placeOrder` with `order.fees = X` (X > 0) and a valid `feeToken` balance/approval. The contract debits `X` fee tokens from the caller and records `_orders[commitment][TRANSACTION_FEES] = X` (`evm/src/apps/IntentGatewayV2.sol:345-362`).
2. Either fill the order same-chain (`fillOrder` → `_fillSameChain`) or cancel it (`cancelOrder` → `_cancelSameChain`/`_cancelFromDest`).
3. Inspect the token list passed into `_withdraw` in each path — it is built solely from `order.inputs`/`escrowedInputs`, never referencing the `TRANSACTION_FEES` key (`evm/src/apps/intentsv2/IntrinsicIntents.sol:116-123,169-184`; `evm/src/apps/intentsv2/ExtrinsicIntents.sol:140-155,202-203,247-250`).
4. Confirm the gateway's fee-token balance remains `X` after order completion/cancellation — `X` is now permanently unreachable to any subsequent transaction, matching the ZetaChain-style stuck-fee pattern.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-362)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L63-64)
```text
        TokenInfo[] memory escrowedInputs = new TokenInfo[](outputsLen);
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-123)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L169-184)
```text
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
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L140-155)
```text
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L202-203)
```text
        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L247-250)
```text
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
        );
```
