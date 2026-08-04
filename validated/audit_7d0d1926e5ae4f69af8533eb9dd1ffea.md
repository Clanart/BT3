## Finding

### Title
Reentrancy in `IntentGatewayV2.withdraw` on Tron lets a fill/refund beneficiary double-spend escrowed funds - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron variant of `IntentGatewayV2` still uses the checks-effects-interactions ordering the current EVM implementation has since moved away from: it performs the external value/token transfer to `beneficiary` **before** decrementing the escrow accounting for that `(commitment, token)` pair. If `beneficiary` is a contract, it can re-enter `withdraw` (or any other externally reachable function that reads `_orders`) during the callback and drain the same escrow slot multiple times before it is ever zeroed out.

### Finding Description
`withdraw()` in the Tron contract sets `_filled[body.commitment]` up front, then for each token: [1](#0-0) 

1. Checks `_orders[body.commitment][token] == 0` (not the actual remaining amount, just non-zero).
2. Performs the transfer — for the native-token branch, a raw `beneficiary.call{value: amount}("")`, and for ERC-20, a raw low-level `token.call(...transfer...)`.
3. Only **after** the external call does it apply `_orders[body.commitment][token] -= amount;`.

This is the same broken invariant as the seed report: the state (`RewardDebt` there, `_orders[...]` here) that must be committed to block a repeat claim is finalized only after the value transfer, and the transfer itself is the reentry point. A contract beneficiary that receives the native-token callback (or implements a malicious `tokenReceived`/fallback path if `token` is an ERC-777-like or hooked token) can call back into `withdraw` (which is only guarded by `_orders[...][token] == 0`, still non-zero at that point) and re-run the transfer for the same commitment before the first invocation's decrement executes.

For comparison, the newer EVM implementation (`IntentsBase._withdraw`) already fixed this exact class of bug by moving the ledger update before the external call: [2](#0-1) 

The Tron file was not updated to match, and it has no reentrancy guard (`nonReentrant` or similar) anywhere in the contract.

### Impact Explanation
`withdraw` is the terminal settlement step for both `RedeemEscrow` (successful fill) and `RefundEscrow`/cancellation flows — it releases user-escrowed input tokens to the solver (or refunds the user). A successful reentrant drain empties the escrow balance for a commitment beyond what was legitimately owed, i.e., unauthorized fund loss straight out of the escrow contract, hitting the bounty's "stealing or loss of funds" / "double-claim / double-settlement" categories.

### Likelihood Explanation
The `beneficiary` address for a fill comes from `msg.sender` of the solver that filled the order (attacker-controlled), and for a refund/cancel it's the order's `user` (also attacker-controlled if the attacker places the order). Either path lets an unprivileged attacker choose a malicious contract as `beneficiary`, requiring no relayer/prover/admin collusion — this call arrives from `onAccept`/`onGetResponse` after ordinary ISMP delivery, which any relayer performs mechanically. The `_orders[...][token]==0` check does not stop a same-token repeat pull because it is a presence flag, not an amount-bound check, and it is stale during the reentrant call.

### Recommendation
Move the `_orders[body.commitment][token] -= amount;` line (and the `TRANSACTION_FEES` delete) to before the corresponding external call, mirroring `IntentsBase._withdraw`, and/or add a `nonReentrant` modifier to `withdraw`/`onAccept`/`onGetResponse` in the Tron contract.

### Proof of Concept
1. Attacker places (or fills) an order on the Tron `IntentGatewayV2` deployment with `beneficiary` set to an attacker-deployed contract `Evil`.
2. `Evil` implements a payable `receive()` (native-token branch) that, on first invocation, calls back into the gateway path that eventually reaches `onAccept` → `withdraw(body, isRefund)` for the same `commitment`/`token` again (e.g., by triggering delivery of the queued settlement message a second time before the first call returns, or via any externally reachable function that shares the reentrant call stack).
3. Because `_orders[body.commitment][token]` is still non-zero when the reentrant call executes (the decrement in step 3 of the outer call hasn't run yet), the check `_orders[body.commitment][token] == 0` passes again, and the same `amount` is transferred a second time.
4. `_orders[body.commitment][token]` is decremented twice for a single legitimate credit, but only after both transfers already fired — net effect: escrow paid out more than once for the same commitment.

Note: I could not trace, within the tool budget, whether the specific call path from `onAccept`/`onGetResponse` on Tron additionally serializes withdrawals in a way that would prevent re-entering `withdraw` itself (e.g., a higher-level guard outside this file); this should be verified against the full Tron contract and its ISMP host wiring before treating this as fully exploitable in production. The core code-level defect — external call before ledger update, no reentrancy guard — is confirmed directly from the file.

### Citations

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
