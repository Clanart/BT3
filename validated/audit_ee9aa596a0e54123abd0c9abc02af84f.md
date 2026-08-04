Confirmed: `_sweepDust` in `IntentsBase.sol` and its Tron/mainline counterparts in `IntentGatewayV2.sol` transfer an arbitrary caller-specified `amount` of an arbitrary `token` straight out of the gateway's raw ERC-20/native balance, with **no accounting check against `_orders[commitment][token]`** (the escrow ledger used by `withdraw()`), unlike `withdraw()` which decrements `_orders[commitment][token]` before releasing funds. This mirrors the report's core invariant break: a general-purpose "sweep leftover value" function that operates on the same balance/token space as funds that must be preserved for a specific purpose (BPT/escrow), without an explicit exclusion/accounting guard.

### Title
Unbounded `SweepDust` can drain escrowed user/solver order funds instead of only protocol dust - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
`_sweepDust` (and the equivalent logic in `evm/tron/contracts/apps/IntentGatewayV2.sol` and `evm/src/apps/IntentGatewayV2.sol`) transfers `req.outputs[i].amount` of `token` to `req.beneficiary` directly out of the gateway contract's balance, with no check that the amount being swept is actually free/unescrowed protocol dust.

### Finding Description
The IntentGateway holds two categories of value in the same raw token balance: (1) escrowed order inputs tracked per-commitment in `_orders[commitment][token]` (released only via `withdraw()`, which explicitly decrements `_orders[commitment][token]` before transferring, [1](#0-0) ), and (2) protocol "dust" — leftover surplus/fees/calldata residue not tied to any open order.

`_sweepDust`, however, makes no distinction between the two — it simply reads `req.outputs[i].amount` and transfers that amount of `token` to `req.beneficiary`: [2](#0-1) 

The Tron mainline variant is identical in structure: [3](#0-2) 

Unlike `withdraw()`, which is bound by `_orders[commitment][token]` and reverts with `UnknownOrder()` if the escrow entry is missing/zero, `_sweepDust` has zero linkage to the `_orders` mapping. The `amount` for each `SweepDust.outputs[i]` is computed **off-chain** by the coprocessor pallet operator/relayer (`sweep_dust` extrinsic on `modules/pallets/intents-coprocessor/src/lib.rs`, which merely forwards whatever `sweep_dust.outputs` it's given), and the contract performs no on-chain reconciliation against currently-open orders for that token: [4](#0-3) 

Because the gateway's ERC-20/native balance for a given token is the sum of (a) all currently-escrowed order inputs awaiting fill/refund and (b) actual protocol dust, and the on-chain sweep path enforces no upper bound derived from the escrow ledger, a sweep whose `amount` is miscalculated (e.g., stale off-chain dust snapshot, race with a newly-placed order between snapshot and dispatch, or reorg) drains tokens that are contractually owed to specific users/solvers under open orders. This is structurally the same class of bug as the BPT-reinvestment issue: a generic value-extraction path lacks an explicit guard preventing it from touching funds that must never leave the contract outside their dedicated release path (`withdraw()`).

### Impact Explanation
If `SweepDust.outputs[i].amount` for a token exceeds the token's true non-escrowed ("dust") balance at execution time — which is entirely possible given the amount is computed off-chain and there's a window between snapshot and cross-chain delivery during which new orders can be placed or existing orders partially filled — escrowed order funds belonging to users/solvers are transferred to the sweep beneficiary instead. This is a direct loss-of-funds path: legitimate order owners cannot later `withdraw()` or refund tokens that no longer exist in the contract, and the transfer would revert only if the *entire* balance is insufficient (not per-order), meaning it can silently consume just enough escrow to break the invariant for the most recently placed/least-covered orders.

### Likelihood Explanation
This does not require a malicious governance actor — it only requires the routine, expected operational flow (the coprocessor computing and dispatching a dust amount from an off-chain snapshot of "excess" balance) to run against a live gateway that keeps receiving new orders and fills between the snapshot and cross-chain message delivery, which given asynchronous ISMP dispatch/delivery timing is a realistic race. The absence of any on-chain guard tying the sweep to `_orders` accounting means correctness depends entirely on the freshness/accuracy of an off-chain computation, with no fail-safe in the contract.

### Recommendation
Bound `_sweepDust` (and the Tron equivalent) to the contract's actual "free" balance rather than trusting a bare off-chain amount: either (a) maintain an on-chain running total of escrowed-per-token balance across all open orders and require `amount <= IERC20(token).balanceOf(address(this)) - totalEscrowed[token]`, or (b) have `_sweepDust` compute the sweepable amount on-chain from a tracked "dust accumulator" per token that is incremented exactly where `DustCollected`/fee/surplus events are emitted, and only ever sweeps from that accumulator instead of the raw balance.

### Proof of Concept
1. User places an order on the source-chain `IntentGatewayV2`, escrowing 10,000 USDC; `_orders[commitment][USDC] = 10_000e6`.
2. Off-chain coprocessor snapshots the gateway's USDC balance, sees `balanceOf(gateway) = 10,050 USDC` (10,000 escrowed + 50 real dust from a prior fill's surplus), and — due to a stale snapshot, race, or miscalculation — computes/dispatches a `SweepDust` with `outputs = [{token: USDC, amount: 10_050e6}]` intending to only take the 50 USDC dust but instead sweeping the full balance.
3. `pallet_intents_coprocessor::sweep_dust` dispatches this unchanged to the gateway; `onAccept` routes to `_sweepDust`, which calls `IERC20(USDC).safeTransfer(beneficiary, 10_050e6)` with no check against `_orders[commitment][USDC]`, as shown in `IntentsBase.sol:579-597`.
4. The user's escrowed 10,000 USDC is now gone from the contract; when the order is later filled/cancelled and `withdraw()` attempts `IERC20(token).safeTransfer(beneficiary, amount)` for the escrowed 10,000 USDC, the call reverts (insufficient balance) or, if other orders' funds cover it, silently pays out of other users' escrow — either way the affected order's funds are unrecoverable through the normal path.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-409)
```text
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L579-597)
```text
    function _sweepDust(SweepDust memory req) internal {
        uint256 outputsLen = req.outputs.length;
        for (uint256 i; i < outputsLen;) {
            TokenInfo memory info = req.outputs[i];
            address token = address(uint160(uint256(info.token)));
            uint256 amount = info.amount;

            if (token == address(0)) {
                (bool sent,) = req.beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(req.beneficiary, amount);
            }
            unchecked {
                ++i;
            }
            emit DustSwept(token, amount, req.beneficiary);
        }
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L652-673)
```text
        } else if (kind == RequestKind.SweepDust) {
            SweepDust memory req = abi.decode(incoming.request.body[1:], (SweepDust));

            uint256 outputsLen = req.outputs.length;
            for (uint256 i; i < outputsLen;) {
                TokenInfo memory info = req.outputs[i];
                address token = address(uint160(uint256(info.token)));
                uint256 amount = info.amount;

                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
            }
        }
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L474-501)
```rust
		#[pallet::call_index(4)]
		#[pallet::weight(T::WeightInfo::sweep_dust())]
		pub fn sweep_dust(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			sweep_dust: types::SweepDust,
		) -> DispatchResult {
			T::GovernanceOrigin::ensure_origin(origin)?;

			// Get gateway info
			let gateway_info =
				Gateways::<T>::get(state_machine).ok_or(Error::<T>::GatewayNotFound)?;

			// Prepare cross-chain request
			let request = RequestKind::SweepDust(sweep_dust.clone());
			let body = request.encode_body();

			// Dispatch cross-chain message
			Self::dispatch(state_machine, gateway_info.gateway, body)?;

			Self::deposit_event(Event::DustSweepInitiated {
				state_machine,
				beneficiary: sweep_dust.beneficiary,
				tokens: sweep_dust.outputs,
			});

			Ok(())
		}
```
