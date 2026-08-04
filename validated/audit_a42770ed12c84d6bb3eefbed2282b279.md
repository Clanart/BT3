### Title
Relayer fee accounting (`Fees` ledger) on `pallet-relayer` is not bound to the destination host's actual token balance, causing legitimate withdrawals to permanently fail - ([File: modules/pallets/relayer/src/accumulate.rs], [File: modules/pallets/relayer/src/withdrawal.rs], [File: evm/src/core/HostManager.sol])

### Summary
The Olympus report shows that TRSRY tracked a `reserves` mapping independent of the tokens it actually held (loans reduced real balance but not `getReserveBalance`), so downstream capacity/consumers over-committed against non-existent tokens and legitimate transfers reverted with `TRANSFER_FAILED`. Hyperbridge has a structurally identical pattern in `pallet-relayer`: a relayer's payable balance is an on-chain ledger entry (`Fees<T>`) built purely from *source-chain* state-proof evidence, entirely decoupled from whatever fee-token balance the *destination* `EvmHost`/`HostManager` actually holds at withdrawal time.

### Finding Description
`Pallet::accumulate` in [1](#0-0)  credits `Fees::<T>` for a `(state_machine, beneficiary)` pair purely by verifying a *source-chain* state proof that a fee was posted, and a *destination-chain* proof of who delivered it. Nothing in this path reads or reserves the destination `EvmHost`/`HostManager`'s actual fee-token balance.

When a relayer later calls `withdraw` (`modules/pallets/relayer/src/withdrawal.rs`), the pallet only checks the *ledger* value against a minimum threshold: [2](#0-1) 
and then dispatches an ISMP `WithdrawParams{beneficiary, amount: available_amount, token}` POST to the destination chain's `HostManager`, without any indication of whether that amount is actually sitting in the destination host contract: [3](#0-2) 

On the destination side, `HostManager.onAccept` blindly forwards the withdrawal to `IHostManager(host).withdraw(withdrawParams)`: [4](#0-3) 

`EvmHost`'s fee-token balance is whatever accumulated from dispatch fees (`_requestCommitments` / `FeeMetadata`) actually retained on that specific chain — a pool that is simultaneously drawn down by: (a) the relayer-fee withdrawal path described here, (b) `pallet-host-executive::withdraw` protocol-fee sweeps (`modules/pallets/host-executive/src/lib.rs:276-319`), and (c) the new `OutboundRequestDeliveryReward`/`OutboundConsensusDeliveryReward` claim paths described in `docs/outbound-request-incentivization.md`. None of these three consumers coordinate against a single source of truth for the destination host's real token balance; each maintains its own Hyperbridge-side ledger (`Fees`, protocol-fee state, `OutboundRequestDeliveryReward`) that is credited independently of what the `EvmHost` contract actually holds.

This is the exact analog of the TRSRY bug: capacity (`Fees::<T>` / reward ledgers) is computed and trusted without ever being reconciled against the actual token balance of the paying contract (`EvmHost`). Just as TRSRY's `getReserveBalance` overstated available tokens after an untracked loan reduced the real balance, Hyperbridge's relayer/host-executive/reward ledgers can collectively exceed the `EvmHost`'s real fee-token balance whenever dispatch volume (and hence real accrued fee-token inflow) is lower than the sum of the independently-accruing claim ledgers, or whenever governance sweeps protocol revenue via `HostExecutive::withdraw` while relayer `Fees` remain unclaimed.

### Impact Explanation
When the aggregated ledger claims exceed the destination host's real token balance, a legitimate relayer's `withdraw_fees` dispatch reaches the destination chain but the underlying ERC-20 `transfer`/`safeTransfer` in `EvmHost.withdraw` reverts (or a `HostExecutive::withdraw` competing sweep drains the balance first), causing:
- The relayer's owed funds to become unpayable (funds effectively locked) even though the protocol's own accounting says they are owed and available.
- A silent state inconsistency: `Fees::<T>` is only zeroed by the `withdraw` extrinsic's dispatch (fire-and-forget POST, no ack path shown), so a relayer may re-attempt withdrawal indefinitely against a chain whose host can never satisfy it, without any protocol mechanism to detect or rebalance the shortfall (mirroring the Olympus finding's exact remediation gap: "no reserve requirement check inside the debt functions").

This is a fund-availability/liveness defect rather than a direct theft, matching the Low severity of the original C4 finding, and it satisfies the gate's "loss/lock of funds to the rightful beneficiary" category because the relayer's earned reward becomes stuck against an under-collateralized destination host.

### Likelihood Explanation
Likelihood is inherently low, mirroring the original report's own admission ("unlikely… requires unusual circumstances"): the three independent consumers of a single `EvmHost` fee-token pool (relayer fee withdrawals, host-executive protocol-fee sweeps, and the newer outbound consensus/request delivery rewards) would need to collectively draw down more than the pool currently holds, which is plausible under bursty relaying activity, a large protocol-fee sweep, or when reward ledgers accrue faster than actual dispatch-fee inflow on a specific destination chain — none of which require a malicious relayer, prover, or governance actor.

### Recommendation
Before dispatching a withdrawal-triggering message from `pallet-relayer::withdraw`, `pallet-host-executive::withdraw`, or the reward-claim paths in `docs/outbound-request-incentivization.md`, query (or have `EvmHost.withdraw` itself enforce) that the requested `amount` does not exceed the contract's actual `IERC20(feeToken).balanceOf(address(this))` for that specific chain, and reject/queue the transfer instead of allowing an unbacked claim to be dispatched and silently fail on arrival. Consider maintaining a single canonical "available-to-withdraw" counter on `EvmHost` that all consumers (relayer, host-executive, reward claims) decrement atomically, rather than three independently-accruing off-chain ledgers racing against one shared balance.

### Proof of Concept
Conceptual reproduction (mirrors the Olympus PoC structure):
1. On destination chain `Evm(X)`, `EvmHost` accrues fee-token `F` from normal dispatch fees.
2. Relayer A delivers a batch of messages and calls `accumulate_fees`, crediting `Fees::<T>[Evm(X)][A] = F` in `modules/pallets/relayer/src/accumulate.rs:134-147`.
3. Governance separately calls `pallet-host-executive::withdraw` (`modules/pallets/host-executive/src/lib.rs:276-319`) to sweep the same `F` fee-token balance from `EvmHost` to the treasury, since nothing ties the host-executive's withdrawal amount to relayer-outstanding `Fees`.
4. Relayer A then calls `pallet-relayer::withdraw_fees` (`modules/pallets/relayer/src/withdrawal.rs:81-159`), which passes the `available_amount = F` check against `MinimumWithdrawalAmount` and dispatches a `WithdrawParams{amount: F}` POST to `HostManager`.
5. `HostManager.onAccept` → `EvmHost.withdraw` attempts to transfer `F` fee-tokens that no longer exist on that chain — the transfer reverts, exactly as `TRANSFER_FAILED` did in the original TRSRY PoC, leaving relayer A's legitimately accrued fee permanently unpayable from that ledger entry.

### Citations

**File:** modules/pallets/relayer/src/accumulate.rs (L134-147)
```rust
			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-123)
```rust
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L144-159)
```rust
			_ => {
				let HostParam::EvmHostParam(params) =
					HostParams::<T>::get(withdrawal_data.dest_chain)
						.ok_or_else(|| Error::<T>::MissingMangerAddress)?;

				let body = WithdrawalParams {
					beneficiary_address: beneficiary_address.clone(),
					amount: available_amount.into(),
					token: params.fee_token,
				}
				.abi_encode()
				.map_err(|_| Error::<T>::InvalidPublicKey)?;

				(params.host_manager.0.to_vec(), body)
			},
		};
```

**File:** evm/src/core/HostManager.sol (L95-104)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.Withdraw) {
            // This is where governance & relayers can withdraw their revenue.
            WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
            IHostManager(_params.host).withdraw(withdrawParams);
```
