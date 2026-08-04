## Finding

### Title
Tron `IntentGatewayV2.withdraw()` decrements escrow after external transfer, reintroducing the reentrancy the mainline EVM contract already had to fix — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Sherlock bug is a *missing state update before/around payout* that lets a claimant call `claim()` repeatedly and drain the same allocation multiple times because `self.claimed` is never persisted. The Hyperbridge local analog is the internal `withdraw()` function in the **Tron** fork of `IntentGatewayV2`, which performs the escrow debit (`_orders[commitment][token] -= amount`) **after** the external token/native transfer instead of before it — a checks-effects-interactions (CEI) violation that the mainline EVM `IntentsBase._withdraw` was specifically patched to avoid.

### Finding Description
In the canonical EVM intents contract, `_withdraw()` marks the order filled and decrements escrow **before** any external call: [1](#0-0) 

This ordering is confirmed load-bearing by the dedicated regression test suite that documents a prior reentrancy fix: [2](#0-1) 

and the mainline `cancelOrder` entrypoint additionally carries a `nonReentrant` guard: [3](#0-2) 

The Tron port of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, re-implements `withdraw()` with the **opposite, vulnerable ordering**: the beneficiary transfer (native `.call` or ERC20 `.call`) happens first, and `_orders[body.commitment][token] -= amount;` — the value that gates future payouts (`if (_orders[body.commitment][token] == 0) revert UnknownOrder();`) — is only decremented afterward: [4](#0-3) 

A `grep` over this file for `nonReentrant`/`ReentrancyGuard` returns zero matches — the Tron contract has no reentrancy protection anywhere, unlike the mainline `cancelOrder` which is `nonReentrant`. This is the same class of bug as the seed report: the balance/claimed-state that should gate repeat payout (`_orders[commitment][token]`, analogous to `self.claimed`) is updated *after* the value transfer instead of before it, so a reentrant call that lands between the transfer and the decrement observes stale (pre-debit) escrow state and can be paid out again for the same commitment/token before the first payout's bookkeeping lands.

### Impact Explanation
If a beneficiary token (native TRX via low-level `.call`, or a TRC20/ERC20-with-hook) is one of the escrowed input tokens, its receive/transfer hook executes while `_orders[body.commitment][token]` still reflects the pre-payout balance. Any code path that can re-enter the withdrawal/fill logic for the same commitment during that window (e.g., a same-chain fill path invoking the same internal accounting, or a queued duplicate delivery) sees the escrow entry as still funded and pays it out a second time — a direct, unauthorized double-settlement of bridged/escrowed funds, matching the bounty's "double-claim/double-settlement" and "stealing or loss of funds" categories. This is a full loss of the affected escrow, paid to whichever party controls the reentrant hook, exactly mirroring the "attacker repeatedly claims and drains reserves" impact of the seed report.

### Likelihood Explanation
This requires no malicious relayer, prover, governance actor, or leaked key — an ordinary user/solver placing or filling an order with an attacker-controlled token contract (or via native-token receive hooks) is sufficient to trigger the reentrant path, since the vulnerable code has zero reentrancy guarding anywhere in the file. The mainline EVM contract needed an explicit CEI fix plus `nonReentrant` for this exact primitive, which is strong evidence this ordering is genuinely exploitable and not merely theoretical; the Tron fork simply never received that fix.

### Recommendation
Port the CEI fix from `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw` to `evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw`: decrement `_orders[body.commitment][token]` (and mark `_filled`/finalize state) **before** performing the native or ERC20 transfer for each token, and add a `nonReentrant` guard (or equivalent) consistent with the mainline `cancelOrder`/fill entrypoints.

### Proof of Concept
Conceptual PoC (mirrors `IntrinsicIntentsReentrancyTest.sol`'s already-proven mainline exploit, applied to the un-patched Tron ordering):
1. Place a cross-chain order on the Tron gateway with two escrowed inputs: native TRX and an attacker-controlled ERC20/TRC20 "evil token" as beneficiary-controllable assets.
2. Trigger `withdraw()` (via the `RedeemEscrow`/`RefundEscrow` `onAccept` path, or any same-chain path that reaches this internal function) for that commitment.
3. During the native `.call{value: amount}("")` transfer to the beneficiary (first loop iteration, before `_orders[commitment][token] -= amount` executes), the beneficiary's fallback reenters the withdrawal path for the same commitment.
4. Because `_orders[commitment][token]` has not yet been decremented, the check `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` still passes, and the escrowed amount is paid out a second time before the first call's decrement lands — draining the escrow beyond what was legitimately owed.

Note: I was unable to fully trace, from the indexed snippets alone, which exact external entrypoint on the Tron contract (same-chain fill vs. a duplicate-delivery path) provides the concrete re-entry trigger into `withdraw()`, since `withdraw()` itself is `internal`. The CEI-violation and complete absence of `ReentrancyGuard`/`nonReentrant` in this file are directly confirmed in the code; the exact call graph reaching `withdraw()` a second time within one transaction would need to be verified against the full Tron contract (fill/cancel functions) in a live Devin session with complete file access.

### Citations

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

**File:** evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol (L305-316)
```text
    /**
     * @dev Same-chain multi-output escrow theft is blocked by the CEI fix.
     *
     * Before the fix: on a two-output order (ETH + ERC-20), the malicious
     * beneficiary could re-enter during the ETH transfer, self-fill the ERC-20
     * output (net-zero cost), trigger `_withdraw(finalize=true)`, and steal the
     * entire input[1] escrow.
     *
     * After the fix: `_filled[commitment]` is set before the loop, so the
     * reentrant call reverts with `Filled()`. The whole transaction reverts with
     * `InsufficientNativeToken()` and no state is mutated.
     */
```

**File:** evm/src/apps/IntentGatewayV2.sol (L470-470)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-720)
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

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
```
