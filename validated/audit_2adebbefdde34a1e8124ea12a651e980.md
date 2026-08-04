### Title
Reentrant escrow drain via checks-effects-interactions violation in `IntentGatewayV2.withdraw()` (Tron) — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The external report's core broken invariant is: a value meant to gate/limit fund movement (`allowance()`) is checked but the corresponding state is never mutated before the withdrawal effect happens, so the "same" balance can be drained repeatedly. The direct Hyperbridge analog is in the Tron build of the Intent Gateway: `withdraw()` performs the external token/native transfer to an attacker-influenced `beneficiary` *before* decrementing the escrow ledger (`_orders[commitment][token] -= amount`), and the existence check only verifies the slot is non-zero, not that it covers `amount`. This is a textbook checks-effects-interactions (CEI) violation that a malicious beneficiary contract can exploit via reentrancy to redeem the same escrow multiple times before it is ever decremented.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, `withdraw()` is invoked from `onAccept()` for `RedeemEscrow`/`RefundEscrow` messages: [1](#0-0) 

The withdrawal loop for each escrowed token performs the transfer and only afterwards decrements the ledger: [2](#0-1) 

Two problems compound here:
1. **Interaction before effect.** `token.call(...transfer...)` (or the raw native `beneficiary.call{value: amount}("")`) executes while `_orders[body.commitment][token]` still holds its pre-withdrawal value. If `beneficiary` is a contract (attacker-controlled, since `beneficiary` is decoded directly from `body.beneficiary`, which for a `RedeemEscrow` is the filling solver and is fully attacker-chosen at fill time), its fallback/receive hook (for native transfers) or `transfer` callback (for ERC-777/hook-style tokens) can re-enter `onAccept`/`withdraw` — or any other externally reachable path that reads `_orders[commitment][token]` — while the balance is still un-decremented.
2. **Weak guard.** The only guard against double-spend is `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` — a presence check, not an amount-bound check, directly mirroring the reported `allowance()` bug where a value that *looks* like a limit does not actually enforce one before the drain occurs, because the "real" state update happens too late (or, as here, is skippable via reentrancy) to stop repeat consumption.

This exact class of bug was previously identified and fixed on the primary EVM contract: the current `IntentsBase.sol` decrements the ledger *before* the external transfer (CEI-correct), and a dedicated regression suite (`IntrinsicIntentsReentrancyTest.sol`) exists specifically to prove reentrant escrow theft is now blocked: [3](#0-2) [4](#0-3) 

The Tron contract is a separately maintained copy of the same protocol logic and was not updated with this fix — it still has the vulnerable transfer-then-decrement ordering, and `onAccept`/`withdraw` carry no reentrancy guard (`onlyHost` only checks the caller is the ISMP host, not re-entrancy).

### Impact Explanation
This satisfies the required Hyperbridge impact class of "stealing or loss of funds" via "unauthorized transaction or execution" / bridge custody logic attack: a solver who fills (or a cancelling user who is refunded) can name a malicious contract as `beneficiary`, and that contract's fallback can re-enter to redeem the same commitment's escrow multiple times, or interleave with other order-processing paths that key off `_orders[commitment][token]`, draining the gateway's escrowed token/native balance beyond what any single order legitimately holds. Because the destination-side accounting (`_orders`) is shared bridge custody, this is fund theft from the protocol's escrow, not merely a griefing/DoS issue, and it is reachable by an ordinary solver/user through the standard `RedeemEscrow`/`RefundEscrow` flow — no malicious relayer, prover, or admin is required, only a malicious `beneficiary` contract address supplied through the normal fill/cancel flow.

### Likelihood Explanation
Likelihood is contingent on whether this Tron file is actually deployed/live rather than dead/reference code, which I could not verify from the index (no deployment manifest or address registry for the Tron build was found in the accessible context). Within the file itself, the precondition is straightforward and requires no privileged actor: any solver filling a cross-chain order, or any user/relayer triggering a destination-side cancel, controls the `beneficiary` value that ends up receiving the callback. Given that the identical bug pattern was found and fixed in the canonical EVM contracts (evidenced by the reentrancy regression tests), the presence of the unfixed pattern in the parallel Tron implementation is a credible divergence-drift bug rather than a stretch.

### Recommendation
Apply the same CEI fix used in `IntentsBase.sol` to the Tron contract: decrement `_orders[body.commitment][token]` (and zero `TRANSACTION_FEES`) *before* making the external call, and add a reentrancy guard around `onAccept`/`withdraw` for defense in depth. Concretely, mirror: [5](#0-4)  — replace `_orders[body.commitment][token] == 0` non-membership check with a bound check against `amount`, and move the `-=`/`delete` state writes ahead of every external `.call`.

### Proof of Concept
1. A cross-chain order is placed and filled by a malicious "solver" contract `Evil`, or a user cancels naming `Evil` as `beneficiary`.
2. The settlement `RedeemEscrow`/`RefundEscrow` message reaches the Tron gateway; `onAccept` calls `withdraw(body, ...)`.
3. Inside the token loop, when `token == address(0)` (native) or the ERC-20 has a transfer hook, the call to `Evil` triggers its fallback before `_orders[body.commitment][token] -= amount;` executes.
4. `Evil`'s fallback re-enters a path that reads/uses `_orders[body.commitment][token]` (still un-decremented) — e.g., by having the source/destination replay the same commitment through another accepted message, or by chaining with `SweepDust`/other token loops in the same call that check the same still-nonzero balance — extracting more value than the commitment was ever escrowed for.
5. Only after all reentrant calls unwind does `_orders[body.commitment][token] -= amount` finally apply once, leaving the ledger inconsistent with the multiple transfers already sent.

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L686-705)
```text
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
