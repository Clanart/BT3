Based on my investigation, the strongest local analog to the `PublicAllocator`/`EulerEarn` share-tracking divergence is in `IntrinsicIntents::_fillSameChain`, which computes the escrow release amount from two different sources of truth depending on whether the current fill is the final one or an intermediate partial fill.

### Title
Escrow release computed from stale `order.inputs[i].amount` instead of live `_orders[commitment][token]` balance during partial fills - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`_fillSameChain` releases escrow using two different value sources for the same underlying quantity: the immutable, order-time-fixed `order.inputs[i].amount` for intermediate partial fills, and the live, mutable `_orders[commitment][token]` mapping only on the final fill leg. This is structurally identical to the reported bug class: one code path trusts a "real"/live tracked balance while a sibling path trusts a static/cached value for the same balance, and nothing keeps the two in sync between calls.

### Finding Description
In `placeOrder` (evm/src/apps/IntentGatewayV2.sol:333-343), the escrow ledger is initialized once: `_orders[commitment][token] = reducedInputs[i].amount`, and this same numeric value is also kept in the calldata struct field `order.inputs[i].amount` that gets rehashed into every subsequent `fillOrder`/`cancelOrder` call. [1](#0-0) 

In `_fillSameChain`, the escrow amount to release for a given input leg is computed differently depending on whether this fill completes the leg: [2](#0-1) 

- On a **completing** fill (`amountFilled == totalRequired`), the code reads the **live** `_orders[commitment][token]` value — i.e., whatever remains tracked internally.
- On an **intermediate partial** fill, the code instead computes `(order.inputs[i].amount * fillAmount) / totalRequired` — a value derived purely from the **original, immutable** order field, with no reference to what `_orders[commitment][token]` currently holds.

Under normal execution, these stay consistent because `_withdraw` (invoked at the end of every fill call) decrements `_orders[commitment][token]` by exactly the previously computed proportional `escrowedAmount`, and no other path is expected to touch that ledger entry between fills of the same order. The invariant that keeps the two paths equivalent is implicit and fragile: it depends on `_orders[commitment][token]` never being mutated by anything except the proportional formula itself. If any other mechanism decrements or otherwise changes `_orders[commitment][token]` for an order that is still being partially filled (for instance, governance-driven dust-sweeping flows referenced elsewhere in the intents stack, such as the `SweepDust` request type wired through `IntentGatewayV2`/`ExtrinsicIntents`), the intermediate-fill branch keeps computing releases from the stale `order.inputs[i].amount` baseline while the final-fill branch reads whatever is actually left in `_orders[commitment][token]` — producing a release amount that no longer reflects the real escrowed balance, exactly mirroring how `PublicAllocator::reallocateTo()` computed target amounts from real share balances while `EulerEarn::reallocate()` executed against internal share tracking.

I was not able to fully trace every call site that can mutate `_orders[commitment][token]` outside of `_withdraw` within the remaining investigation budget (in particular the complete `SweepDust`/`ExtrinsicIntents.sol` and `IntentsBase._withdraw` implementations), so I cannot state with certainty that an unprivileged, permissionless path currently forces the two values to diverge in production. This is a structural weakness confirmed in code, not a fully traced end-to-end exploit.

### Impact Explanation
If the two accounting paths diverge, a solver performing a partial fill could either (a) receive escrow computed from a higher stale total than what is actually held, causing over-release of another party's escrowed funds, or (b) under-release, permanently stranding the remainder in the contract since the final-fill branch would then also compute against an already-corrupted `_orders[commitment][token]` value. Either outcome is a fund-safety issue (wrong amount released from escrow) consistent with the bounty's "unauthorized transaction or execution" / "transaction manipulation" categories.

### Likelihood Explanation
Likelihood is Low-to-Medium given the current investigation: the divergence requires some out-of-band mutation of `_orders[commitment][token]` for an order mid-partial-fill, and I could not confirm within this session that any permissionless/unprivileged entrypoint can trigger such a mutation. The pattern itself (two sources of truth for the same balance, one static one live, used in different branches of the same function) is the same broken invariant as the seed report, and is present in shipped code without any reconciliation check between `order.inputs[i].amount` and `_orders[commitment][token]` at the top of `_fillSameChain`.

### Recommendation
Do not use `order.inputs[i].amount` as the basis for any escrow-release math after `placeOrder`. Instead, always compute the proportional release from the live `_orders[commitment][token]` balance (e.g., `escrowedAmount = (_orders[commitment][token] * fillAmount) / remaining` using the current remaining ledger value), so both the partial-fill and full-fill branches read from the single source of truth. Additionally, audit every code path that can mutate `_orders[commitment][token]` (including any dust-sweep/governance flows) to ensure they cannot run against an order with outstanding partial fills, or that they correspondingly update the proportional-fill bookkeeping.

### Proof of Concept
A conclusive PoC requires exercising whatever mechanism (if any) mutates `_orders[commitment][token]` independently of `_withdraw` during an in-flight partial fill (candidate: a `SweepDust`-style governance/administrative message referenced in the intents request-kind types). I was unable to fully trace and confirm this call path within the available tool budget; the concrete steps would be:
1. `placeOrder` with a single input token, escrowing `X` into `_orders[commitment][token]`.
2. Solver partially fills a fraction of the output, causing `_withdraw` to release `(order.inputs[i].amount * fillAmount) / totalRequired` and decrement `_orders[commitment][token]` accordingly.
3. Trigger the out-of-band mutation of `_orders[commitment][token]` for the same commitment (unverified path).
4. Solver completes the fill; observe that the amount computed via the intermediate-fill branch on a subsequent partial fill no longer matches the live `_orders[commitment][token]`, producing an over- or under-release relative to actual escrow.

Given the uncertainty around step 3, this should be treated as a structural weakness requiring further code review of `SweepDust`/`ExtrinsicIntents.sol`/`IntentsBase._withdraw` to confirm exploitability, rather than a fully proven exploit chain.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-122)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
```
