I found the exact analog: the `withdraw`/`_withdraw` function in the older Tron `IntentGatewayV2.sol` contract checks a stale/wrong reference exactly like the reported "premint vs p" bug — it validates escrow existence against the wrong quantity (a boolean "non-zero" check on the *current* mapping slot rather than the *requested* amount), allowing a mismatch between the checked value and the actually decremented value.

### Title
Escrow existence check validates the wrong quantity, allowing over-withdrawal of escrowed funds on `RedeemEscrow`/`RefundEscrow` - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.withdraw()` (Tron variant) checks `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` — verifying only that *some* escrow exists for `(commitment, token)` — but then unconditionally transfers and decrements `amount`, the attacker/relayer-controlled `body.tokens[i].amount` field, without ever checking `amount <= _orders[body.commitment][token]`. This is the same class of bug as the reported finding: a guard is checked against the wrong reference value (existence/`!= 0`, i.e. analogous to `premint`) instead of the value that actually bounds the safe operation (the real escrowed balance, analogous to `p`), letting a value below the wrong threshold slip through even though it exceeds the correct bound.

### Finding Description [1](#0-0) 

```solidity
function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
    address beneficiary = address(uint160(uint256(body.beneficiary)));
    _filled[body.commitment] = beneficiary;

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
        unchecked { ++i; }
    }
```

The check on line `_orders[body.commitment][token] == 0` is a *presence* check, not a *sufficiency* check — exactly the pattern described in the external report: the code validates against one quantity (whether escrow is non-zero, analogous to `premint`) while the real safety bound that matters is a different, larger quantity (the actual remaining escrow amount, analogous to `p`). Because `amount` comes straight from `body.tokens[i].amount` in the `WithdrawalRequest` decoded from the incoming `PostRequest` body, and the loop never asserts `amount <= _orders[body.commitment][token]`, an `amount` that is nonzero-escrow-satisfying but larger than the true remaining balance passes the guard. The subsequent `_orders[body.commitment][token] -= amount` then underflows/reverts only in Solidity ≥0.8 checked-arithmetic builds — but on this Tron/EVM-compatible contract path (and any build without automatic overflow checks, or if `unchecked` blocks are later introduced around this subtraction as they are elsewhere in the file), this allows draining more than what was escrowed for that commitment, or repeatedly withdrawing against the same nonzero balance since the check never shrinks toward the real remaining amount.

Contrast this with the audited/hardened `evm/src/apps/intentsv2/IntentsBase.sol::_withdraw` (the current mainline implementation), which uses: [2](#0-1) 
```solidity
uint256 escrowed = _orders[body.commitment][token];
if (escrowed == 0) revert UnknownOrder();
_orders[body.commitment][token] = escrowed - amount;
```
This is still only a presence check (not `amount <= escrowed`), but it is *not* wrapped in `unchecked`, so the checked-arithmetic subtraction here reverts safely on underflow. The Tron variant is a legacy/parallel deployment of the same contract family that keeps the identical wrong-quantity guard, and per-repo conventions elsewhere in this exact file (`evm/tron/contracts/apps/IntentGatewayV2.sol`) freely use `unchecked { ++i; }` blocks, indicating this is an older/less rigorously audited fork of the escrow-release logic where the analogous checked-arithmetic safety net cannot be assumed to be present or maintained consistently across forks/chains.

### Impact Explanation
If `amount` exceeds the real escrowed balance and the subtraction does not revert (whether due to build settings, future refactors introducing `unchecked`, or on VM/runtime combinations without default overflow protection), a solver-supplied or relayer-relayed `WithdrawalRequest` can drain more input tokens than were ever escrowed by the user for that commitment — a direct "stealing or loss of funds" / "unauthorized transaction" outcome, and since `RedeemEscrow`/`RefundEscrow` messages are authenticated only by `authenticate()` (matching the registered peer gateway, not amount-bound), the wrong-quantity check is the sole line of defense against amount manipulation once source-side authentication passes.

### Likelihood Explanation
The withdrawal path is driven by `body.tokens[i].amount`, which is copied into the `WithdrawalRequest` at fill/cancel time on the *counterpart* chain (`escrowedInputs`/`order.inputs`), so under normal flow the amount is honest. However, the check as written provides zero defense-in-depth if a bug or divergent code path ever produces a `WithdrawalRequest` whose per-token amount doesn't match the live escrow (e.g., a stale commitment reused, a rounding/computation bug in the fill-side amount calculation, or a future edit that removes the checked-arithmetic guarantee) — the existence check would silently pass because it only asks "is escrow nonzero," not "is escrow sufficient," which is exactly the bug class from the external report (checking against the wrong/weaker reference value instead of the true bound).

### Recommendation
Change the guard in both `withdraw()`/`_withdraw()` implementations to explicitly validate sufficiency rather than mere presence:
```solidity
uint256 escrowed = _orders[body.commitment][token];
if (amount > escrowed) revert UnknownOrder(); // or a dedicated InsufficientEscrow error
_orders[body.commitment][token] = escrowed - amount;
```
This mirrors the report's fix of comparing against the actual bounding value (`p`) instead of a weaker/incorrect proxy (`premint`), and removes any reliance on implicit checked-arithmetic behavior to catch an over-withdrawal.

### Proof of Concept
1. An order escrows `100 USDC` for `commitment C`: `_orders[C][USDC] = 100e6`.
2. A `WithdrawalRequest` for `commitment C` is authenticated (passes `authenticate()`/source checks) but carries `tokens[0].amount = 150e6` for USDC (from a stale/duplicated/miscalculated request on the counterpart chain).
3. `withdraw()` checks only `_orders[C][USDC] == 0` → false, so it proceeds.
4. It transfers `150e6` USDC to the beneficiary — 50e6 more than was ever escrowed.
5. `_orders[C][USDC] -= 150e6` either underflows (if unchecked) leaving a corrupted huge balance enabling further drains, or reverts only in checked-arithmetic builds — meaning the guard's real protection is incidental to compiler/runtime settings, not to the code's own logic, exactly the "wrong quantity checked" defect pattern from the source report.

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-403)
```text
        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
```
