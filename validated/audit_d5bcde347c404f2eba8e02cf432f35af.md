## Analysis Confirmed

I found a direct local analog: `IntrinsicIntents.sol` (same-chain fills) correctly computes the release amount from the **actual escrowed balance** (`_orders[commitment][token]` / a proportional share of it), but `ExtrinsicIntents.sol` (cross-chain fills/cancels) passes the **full pre-fee `order.inputs` amounts** into `WithdrawalRequest.tokens`, which is then fed straight into `IntentsBase._withdraw`'s checked subtraction `_orders[...] = escrowed - amount`. Since `placeOrder` only escrows `reducedInputs[i].amount = originalAmount - protocolFee` whenever a protocol fee is configured, `amount > escrowed` for every fee-bearing cross-chain order, causing a guaranteed underflow revert — the exact same bug class as the reported `VaultFundManager.addFundsAndFulfillRedeem` underflow, but broader in impact (it isn't limited to "first deposit," it hits every fee-bearing cross-chain order, every time).

### Title
Cross-Chain Fill/Cancel Paths Use Pre-Fee `order.inputs` Instead of Escrowed Balance, Causing Underflow-Revert and Permanent Fund Lock - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`IntentsBase._withdraw` releases escrow with `_orders[commitment][token] = escrowed - amount`, a checked subtraction that reverts if `amount > escrowed`. The same-chain path (`IntrinsicIntents._fillSameChain`, `_cancelSameChain`) correctly derives `amount` from the actual escrowed value in `_orders`. The cross-chain path (`ExtrinsicIntents._fillCrossChain`, `_cancelFromSource`, `_cancelFromDest`) instead builds `WithdrawalRequest.tokens` directly from `order.inputs` (the full, pre-protocol-fee amount), while `placeOrder` only ever escrows the fee-reduced amount for that commitment/token.

### Finding Description
In `IntentsBase.sol`'s order placement (shared logic, mirrored in `evm/tron/contracts/apps/IntentGatewayV2.sol#L342-L463`), when `protocolFeeBps > 0`:
```
reducedAmount = originalAmount - protocolFee
_orders[commitment][token] += reducedAmount   // escrow stores the REDUCED amount
``` [1](#0-0) 

For same-chain flows, `_fillSameChain`/`_cancelSameChain` release exactly what's in `_orders` (`escrowedAmount`), so the accounting is always internally consistent: [2](#0-1) [3](#0-2) 

For cross-chain flows, `ExtrinsicIntents._fillCrossChain` dispatches a `RedeemEscrow` message whose `tokens` field is `order.inputs` — the **original, pre-fee** amount, not the reduced value that was actually escrowed: [4](#0-3) 

Similarly, `_cancelFromSource` (GET-verified cancel, resolved via `onGetResponse` → `_withdraw`) and `_cancelFromDest` (`RefundEscrow` message) both carry `tokens: order.inputs`: [5](#0-4) [6](#0-5) 

When the resulting `WithdrawalRequest` reaches `_withdraw`, the check only guards against zero escrow, not against `amount` exceeding it, before doing checked subtraction:
```solidity
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;   // underflow-reverts when amount > escrowed
``` [7](#0-6) 

Because `amount = order.inputs[i].amount` (full) while `escrowed = reducedInputs[i].amount` (post-fee, strictly smaller whenever `protocolFeeBps > 0`), the subtraction always underflows and Solidity 0.8's checked arithmetic reverts the transaction — deterministically, on every attempt, for every cross-chain order placed while a protocol fee was configured.

### Impact Explanation
This blocks legitimate, unprivileged users from ever completing the redeem/refund leg of a cross-chain intent whenever `_params.protocolFeeBps` or a `_destinationProtocolFees[dest]` override is non-zero at order placement time:
- A solver who fills a cross-chain order can never collect the escrowed input tokens on the source chain (`RedeemEscrow` → `onAccept` → `_withdraw` always reverts).
- A user who cancels an unfilled/expired cross-chain order can never get a refund (`_cancelFromSource`/`_cancelFromDest` → `RefundEscrow`/GET-response → `_withdraw` always reverts).
- Funds are permanently locked in the gateway contract — there is no retry path since the underflow is deterministic for that commitment (the escrow value never changes once set), matching the "underflow blocks redemption, funds get stuck" impact of the seed report, but with wider blast radius (every cross-chain order under a live fee, not just a first-deposit edge case).

### Likelihood Explanation
High. `_params.protocolFeeBps`/`_destinationProtocolFees` is an ordinary governance-configurable parameter (`UpdateParams`), not an edge case — it exists specifically to be turned on. Any deployment that charges a nonzero cross-chain protocol fee (a normal, expected production configuration) triggers this on 100% of cross-chain fills and cancels, with no attacker action required beyond simply using the intent gateway as intended.

### Recommendation
In `ExtrinsicIntents._fillCrossChain`, `_cancelFromSource`, and `_cancelFromDest`, build the `WithdrawalRequest.tokens` amounts from the actual escrowed balance (`_orders[commitment][token]`) rather than `order.inputs[i].amount`, mirroring the pattern already used in `IntrinsicIntents._fillSameChain`/`_cancelSameChain`. Alternatively, change `_withdraw` to clamp `amount` to `escrowed` (`amount = amount > escrowed ? escrowed : amount`) so any mismatch degrades gracefully instead of reverting and permanently locking funds.

### Proof of Concept
1. Governance sets `_params.protocolFeeBps = 100` (1%) via `UpdateParams`.
2. User calls `placeOrder` for a cross-chain order with `order.inputs[0].amount = 1000` USDC on the source chain. `placeOrder` computes `protocolFee = 10`, `reducedAmount = 990`, and stores `_orders[commitment][USDC] = 990` [8](#0-7) , while `commitment = keccak256(abi.encode(order))` is computed using the reduced inputs.
3. A solver fills the order on the destination chain via `_fillCrossChain`, which dispatches `RedeemEscrow` with `tokens: order.inputs` → `amount = 1000` [4](#0-3) .
4. On the source chain, `onAccept` decodes the message and calls `_withdraw`, which reads `escrowed = _orders[commitment][USDC] = 990`, passes the `escrowed == 0` check, then executes `990 - 1000` → reverts with a Solidity `Panic(0x11)` arithmetic-underflow error [7](#0-6) .
5. The solver can never claim the escrowed input tokens; if the user instead tries to cancel after expiry, `_cancelFromDest`/`_cancelFromSource` hit the identical `990 - 1000` underflow via `_withdraw`, so the refund path is equally dead. The 990 USDC (and the 10 USDC fee already collected as dust) remain stuck in the source-chain gateway contract indefinitely.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L353-364)
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
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-121)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L140-147)
```text
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
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
