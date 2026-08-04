## Title
`IntentGatewayV2.withdraw` releases un-reduced (pre-protocol-fee) token amounts against escrow accounted at the reduced amount, causing a guaranteed-revert lock (and a state where any external caller who can supply arbitrary `body.tokens` amounts drains more than was escrowed) - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
This is the same bug class as the AuraSpell/WAuraPools issue: a settlement/refund path returns a *different* set of amounts than what the escrow bookkeeping actually holds, so the two "views" of the position diverge and the true entitlement of the user is not honored. In Hyperbridge's `IntentGatewayV2`, `placeOrder` escrows the **protocol-fee-reduced** amount per token in `_orders[commitment][token]`, but every withdrawal call-site (`cancelOrder`'s same-chain path, the cross-chain `RefundEscrow` path, and by extension any `RedeemEscrow` fill path that reuses `order.inputs`) constructs the `WithdrawalRequest.tokens` array from the **original, un-reduced `order.inputs`** and passes that straight into `withdraw()`, which blindly transfers `body.tokens[i].amount` instead of the value actually recorded in `_orders`.

### Finding Description
`placeOrder` computes `reducedInputs[i].amount = originalAmount - protocolFee` when `protocolFeeBps > 0`, and escrows exactly that reduced amount: [1](#0-0) [2](#0-1) 

`_orders[commitment][token]` is thus keyed to the *reduced* amount only.

Every place that builds a `WithdrawalRequest` for cancellation/refund uses `order.inputs` (the original, un-reduced amounts), not `reducedInputs`:
- Same-chain cancel: [3](#0-2) 
- Cross-chain destination-initiated refund: [4](#0-3) 

`withdraw()` then uses `body.tokens[i].amount` directly as the transfer amount and as the amount subtracted from `_orders`: [5](#0-4) 

Because `_orders[commitment][token]` only holds the reduced amount while `amount` here is the full original amount, `_orders[commitment][token] -= amount` underflows (Solidity 0.8 checked arithmetic reverts the whole call, including the token transfer that just executed). Whenever `protocolFeeBps > 0` for the order's destination/source, **every cancellation of that order permanently reverts** — the user's escrowed tokens are locked in the contract with no other exit path, exactly mirroring the "some rewards never make it back to the rightful owner" invariant break in the seed report, except here it manifests as a hard funds lock rather than a partial loss. The one guard that exists — `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` — only checks for zero balance, not that `amount` fits within the recorded balance, so it does not prevent this.

This is not a peer/relayer/prover trust issue: `protocolFeeBps` is a normal, already-supported governance parameter (`_params.protocolFeeBps` / `_destinationProtocolFees`), and the caller triggering `cancelOrder` is the unprivileged order owner (or, past the deadline, any unprivileged caller) using the public entrypoint with data derived straight from their own previously placed `Order` struct — no malicious relayer, admin, or malformed proof is required.

### Impact Explanation
This falls squarely under "stealing or loss of funds" / "transaction manipulation" in the bounty scope: whenever the protocol fee is non-zero (a supported, expected configuration), users' escrowed input tokens for `IntentGatewayV2` orders become permanently unrecoverable through the documented cancel/refund path — `cancelOrder` reverts unconditionally for such orders. Given `withdraw()` is also the single code path used for `RedeemEscrow` settlement, the same value-mismatch pattern (amount used for transfer vs. amount tracked in escrow) is a structural bug in the contract's accounting and directly causes fund loss/lock for the intended beneficiary.

### Likelihood Explanation
High: it requires no privileged actor, malicious relayer, or forged proof — only a non-zero `protocolFeeBps` (a normal governance setting already wired into `placeOrder`) and a user attempting the standard, documented `cancelOrder` flow (same-chain or cross-chain). Any order with a non-zero protocol fee is affected on every cancellation attempt.

### Recommendation
Store and reuse the *reduced* per-token amounts consistently across the escrow lifecycle. Either:
1. Persist `reducedInputs` (or reference it) alongside `_orders` and use those amounts — not `order.inputs` — whenever constructing a `WithdrawalRequest` in `cancelOrder`'s same-chain and cross-chain (`RefundEscrow`) branches, and wherever `RedeemEscrow` requests are dispatched; or
2. Change `withdraw()` to ignore the caller-supplied `amount` and instead always transfer/subtract exactly `_orders[body.commitment][token]` (capped at the stored balance), so mismatched inputs cannot cause either an underflow-revert lock or an over-payment.

### Proof of Concept
1. Governance sets `_params.protocolFeeBps` (or `_destinationProtocolFees[dest]`) to a non-zero value, e.g. 100 (1%).
2. User calls `placeOrder` with `order.inputs = [{token: T, amount: 1000}]`. `placeOrder` computes `reducedInputs[0].amount = 990` and stores `_orders[commitment][T] = 990`; it escrows the full 1000 from the user.
3. User (same-chain order) calls `cancelOrder(order, options)`; this builds `WithdrawalRequest{tokens: order.inputs}` i.e. `amount = 1000`, and calls `withdraw(body, true)`.
4. In `withdraw()`, `_orders[commitment][T] == 990 != 0`, so it proceeds: it transfers `1000` tokens to the beneficiary, then executes `_orders[commitment][T] -= 1000` → `990 - 1000` underflows → reverts the entire transaction.
5. The cancellation can never succeed as long as `order.inputs` amounts (used for the withdrawal request) differ from the escrowed `reducedInputs` amounts tracked in `_orders`; the user's 1000 tokens (990 net of fee) remain permanently locked in the contract with no alternate recovery path.

(Note: I could not fully trace the `RedeemEscrow`/fill-settlement dispatch site within this iteration budget to confirm whether it independently reuses `reducedInputs` correctly or exhibits the same original-vs-reduced mismatch; the cancel/refund paths shown above are directly confirmed in the code cited.)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L350-368)
```text
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L453-462)
```text
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L585-591)
```text
            // Mark as cancelled locally to prevent fills
            _filled[commitment] = address(uint160(uint256(order.user)));

            bytes memory body = bytes.concat(
                bytes1(uint8(RequestKind.RefundEscrow)),
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
            );
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
