## Title
Tron `IntentGatewayV2.withdraw` transfers escrowed tokens before decrementing the escrow ledger, enabling reentrant double-withdrawal - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The external bug report's core invariant is: *an operation performs a fund-moving side effect using a stale/unchecked internal balance, and only updates accounting after the fact*. The `initiateRedeem`/`reward` bug is a benign version of this (unnecessary withdrawal, no loss). The Hyperbridge Tron `IntentGatewayV2.withdraw` function contains the dangerous version of the same defect: it performs the external value transfer to an attacker-influenceable `beneficiary` **before** decrementing `_orders[commitment][token]`, breaking checks-effects-interactions and creating a reentrancy window in which the stale (not-yet-decremented) escrow balance can be spent again.

### Finding Description
In the canonical EVM implementation (`evm/src/apps/intentsv2/IntentsBase.sol`), `_withdraw` correctly follows checks-effects-interactions: the escrow mapping is decremented **before** the external call: [1](#0-0) 

The Tron variant of the same contract (`evm/tron/contracts/apps/IntentGatewayV2.sol::withdraw`) inverts this order — it only checks that escrow is non-zero, performs the native/ERC20 transfer via a raw `.call`, and decrements the escrow mapping **afterward**: [2](#0-1) 

Because the native-token branch uses `beneficiary.call{value: amount}("")`, and `beneficiary` is derived directly from `body.beneficiary` (attacker/solver-controlled address embedded in the cross-chain `WithdrawalRequest`), a beneficiary contract can execute arbitrary code during that call — including re-entering the gateway (e.g., via another pending `onGetResponse`/`onAccept` delivery, or any other order-processing entry point that reads `_orders[commitment][token]`) while `_orders[commitment][token]` still holds its **pre-transfer** value. The guard `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` only checks for non-zero, not for sufficiency against a running total, so a reentrant call sees the same "escrowed" balance as legitimate and pays out again before the original call's `_orders[...] -= amount` ever executes.

This directly generalizes the reported bug class: the transfer executes unconditionally against a balance that has not yet been reconciled, exactly like `initiateRedeem` calling `withdrawDefault` without checking the vault's real, up-to-date balance — except here the unchecked, stale state is exploitable for double-payout rather than merely suboptimal yield.

### Impact Explanation
This allows a malicious solver/beneficiary to drain escrowed order inputs (and/or the accumulated transaction fee balance) for a commitment by reentering during the fund-transfer callback, receiving multiple payouts against a single escrow record. This is fund theft from the bridge custody/escrow (`_orders` mapping) on the Tron deployment of the Intent Gateway — a direct match for "stealing or loss of funds" and "double-settlement" in the bounty's impact gate.

### Likelihood Explanation
Medium-High: the attacker only needs to be a solver filling an order (or the destination-side actor triggering a `RefundEscrow`/`RedeemEscrow` withdrawal) with a `beneficiary` address that is a contract they control, and no privileged role, relayer compromise, or malicious proof is required — the withdrawal path (`onAccept`/`onGetResponse`) is a standard authenticated ISMP delivery that any relayer can carry, and the reentrancy trigger is simply the beneficiary contract's `receive`/fallback executed during the native-token payout.

### Recommendation
Reorder `withdraw` in `evm/tron/contracts/apps/IntentGatewayV2.sol` to decrement `_orders[body.commitment][token]` **before** performing the external call (mirroring `IntentsBase.sol::_withdraw`), and add a reentrancy guard on all externally reachable entry points that can invoke `withdraw` (`onAccept`, `onGetResponse`), consistent with the `nonReentrant` protection already used elsewhere in the codebase (e.g., `fillOrder`/`cancelOrder` in `evm/src/apps/IntentGatewayV2.sol`).

### Proof of Concept
1. Attacker places/solves an order such that a `WithdrawalRequest` will name a contract they control as `beneficiary`, with a native-token (`token == address(0)`) component escrowed for the commitment.
2. When the withdrawal message is delivered (via `onAccept` or `onGetResponse`), `withdraw()` reaches the native branch and calls `beneficiary.call{value: amount}("")`.
3. The attacker's contract `receive()` re-enters the gateway's message-processing path for the same commitment/token before `_orders[body.commitment][token] -= amount` executes on the original call frame.
4. Because `_orders[body.commitment][token]` still reads its pre-decrement (non-zero) value, the reentrant call passes the `== 0` check and transfers the escrowed amount a second time.
5. Both transfers complete before either `_orders[...] -= amount` statement underflows/reverts, since each call frame independently reads the still-nonzero balance prior to its own decrement — net effect: escrow paid out more than once for the same commitment.

Note: I could not execute this in a live test environment; verification is based on static reading of `withdraw()`'s call/state-update ordering and comparison against the corrected ordering in `IntentsBase.sol`. Confirming exact reentrant call-site reachability (which specific public function can be re-invoked mid-call) would benefit from a Devin session with full repo/tooling access to trace the ISMP host's call graph and run a PoC test in the Tron Foundry/Hardhat harness.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-409)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
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
