### Title
Relayer fee balance denominated in a stale `feeToken` can be paid out in the newly-configured `feeToken` at face value - ([File: `modules/pallets/relayer/src/withdrawal.rs`])

### Summary
`pallet-ismp-relayer` accumulates each relayer's unclaimed cross-chain delivery fee as a single scalar `U256` per `(StateMachine, address)` pair, with no record of *which* fee token that amount was denominated in. When a relayer withdraws, the pallet re-reads the *current* `fee_token` configured for that `StateMachine` from `pallet-ismp-host-executive::HostParams` and packages the stale raw `U256` with that current token address. If the EVM host on that chain has legitimately swapped its `feeToken` (a documented, supported operation) between the time the fee was accumulated and the time it is withdrawn, the relayer's stale balance — raw units of the *old* token — is shipped to `EvmHost.withdraw()` tagged as the *new* token, and transferred at face value with no re-denomination. This is the exact bug class from the external report (`fundingFees` mixing multiple assets in a single accounting scalar): a single number is reused across two mutually-incompatible token contexts.

### Finding Description
- `Fees<T>` is a plain `StorageDoubleMap<StateMachine, Vec<u8>, U256>` — one scalar per chain per relayer, with no token dimension: [1](#0-0) 

- Accumulation stores the raw fee value read from the *source* chain's on-chain `FeeMetadata`/`RequestMetadata` (i.e. denominated in whatever ERC-20 that chain's `EvmHost.feeToken()` was at dispatch time), keyed only by `state_machine`: [2](#0-1) 

- At withdrawal time, the pallet fetches the *current* `fee_token` for that same `StateMachine` key from `HostParams` and pairs it with the *stale* `available_amount`: [3](#0-2) 

- `withdraw_fees`/`accumulate_fees` are unsigned, permissionless entry points (`ensure_none(origin)`), reachable by any relayer holding a valid delivery-proof/withdrawal signature — no privileged role is required to trigger the payout: [4](#0-3) 

- On the EVM side, governance is explicitly permitted to change `feeToken` as long as the *host contract's own balance* of the old token is zero — this check has no knowledge of, and does not protect, Hyperbridge's separate `Fees` ledger of unclaimed relayer balances denominated in that old token: [5](#0-4) 

- The withdrawal message is then executed blindly: `HostManager.onAccept` forwards `WithdrawParams{token, amount, beneficiary}` straight to `EvmHost.withdraw()`, which transfers `amount` units of whichever `token` is named — no comparison against the token that was actually escrowed when the fee was earned: [6](#0-5) [7](#0-6) 

Existing guards do not stop this path: the `CannotChangeFeeToken` check in `setHostParams` only inspects the EVM host's *own* live balance of the old token on that single chain — it cannot see, and was never designed to see, the parallel `Fees<T>` ledger living on Hyperbridge (a different chain), which still holds raw, old-token-denominated balances for relayers who haven't withdrawn yet.

### Impact Explanation
This directly falls under the "bridged assets... relayer rewards... must move exactly once and only to the rightful beneficiary and amount" pivot. A relayer's legitimately earned but not-yet-withdrawn fee, quantified in units of token A (e.g. 6-decimal USDC), is transferred out as the *same raw integer* of token B (e.g. 18-decimal DAI, or any newly configured stablecoin) once the host's `feeToken` is swapped. Depending on relative decimals/value between the old and new token, this either:
- massively overpays the relayer at the expense of the `EvmHost`'s new-token reserves (drained funds / value theft from the protocol and other stakeholders), or
- massively underpays the relayer, permanently losing the value of fees they legitimately earned (loss of funds).

Either direction is an unauthorized/incorrect transfer of value with no way for the withdrawing party or the protocol to prevent it once the fee-token swap has occurred, since the accounting layer records no token identity at all.

### Likelihood Explanation
Fee-token migrations on an `EvmHost` are an explicitly documented, expected maintenance operation (see `docs/content/developers/evm/bandwidth/governance.mdx` describing the analogous, acknowledged "stale balances of an old fee token" problem for `BandwidthManager`, which was fixed there by naming the token explicitly in the withdrawal payload). The relayer fee path has no equivalent fix: the withdrawal call is fully public/unsigned and reachable by any relayer at any time after such a swap, with no code path re-validating or re-denominating `available_amount` against the token that was current when the balance accrued. The only precondition is an ordinary, sanctioned governance action (a fee-token swap) that the codebase itself supports and performs without accounting for outstanding relayer balances on Hyperbridge.

### Recommendation
Key `Fees<T>` by `(StateMachine, fee_token, address)` (or store the escrowed token address alongside each accumulated balance) so that a balance accumulated under one fee token can never be paid out denominated in a different token. Alternatively, snapshot and persist the fee token used at accumulation time per-entry, and require `withdraw()` to use that snapshot rather than re-reading the live `HostParams::fee_token`. `setHostParams`'s `feeToken` swap guard should also be extended (or a migration path enforced) so a chain's fee token cannot be changed while `pallet-ismp-relayer::Fees` still has unclaimed non-zero balances for that `StateMachine`.

### Proof of Concept
1. On EVM chain `X`, `EvmHost.feeToken()` is `TokenA` (e.g. USDC, 6 decimals). A relayer delivers messages from chain `X` and legitimately accumulates `Fees::<T>::get(StateMachine::Evm(X), relayer) = 1_000_000` (i.e. 1 USDC) via `accumulate_fees` (see `modules/pallets/relayer/src/accumulate.rs:353-368`).
2. Governance later drains chain `X`'s `EvmHost` balance of `TokenA` to zero (e.g. by processing all pending withdrawals) and calls `updateHostParams` to set `feeToken = TokenB` (e.g. DAI, 18 decimals) — allowed because `EvmHost.sol:617-621` only checks the host's own `TokenA` balance, not Hyperbridge's `Fees` ledger.
3. `pallet-ismp-host-executive::HostParams` for `StateMachine::Evm(X)` is updated to reflect `fee_token = TokenB`.
4. The relayer calls the permissionless `withdraw_fees` extrinsic for `dest_chain = StateMachine::Evm(X)`. `withdrawal.rs:116` reads the stale `available_amount = 1_000_000`; `withdrawal.rs:149-153` builds `WithdrawalParams{ amount: 1_000_000, token: TokenB, beneficiary }`.
5. Hyperbridge dispatches this to `HostManager.onAccept` → `EvmHost.withdraw()`, which executes `IERC20(TokenB).safeTransfer(beneficiary, 1_000_000)` — i.e. `0.000000000000000001` DAI's worth of raw units at 18-decimals interpretation is either meaninglessly small or, if the token pair is reversed (going from an 18-decimal to a 6-decimal token), grossly overpays, transferring far more value than the relayer ever earned, drained straight from the `EvmHost`'s `TokenB` balance.

### Citations

**File:** modules/pallets/relayer/src/lib.rs (L111-122)
```rust
	/// double map of address to source chain, which holds the amount of the relayer address
	#[pallet::storage]
	#[pallet::getter(fn relayer_fees)]
	pub type Fees<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		StateMachine,
		Blake2_128Concat,
		Vec<u8>,
		U256,
		ValueQuery,
	>;
```

**File:** modules/pallets/relayer/src/lib.rs (L350-368)
```rust
		#[pallet::call_index(0)]
		#[pallet::weight({1_000_000})]
		pub fn accumulate_fees(
			origin: OriginFor<T>,
			withdrawal_proof: WithdrawalProof,
		) -> DispatchResult {
			ensure_none(origin)?;
			Self::accumulate(withdrawal_proof)
		}

		#[pallet::call_index(1)]
		#[pallet::weight({1_000_000})]
		pub fn withdraw_fees(
			origin: OriginFor<T>,
			withdrawal_data: WithdrawalInputData,
		) -> DispatchResult {
			ensure_none(origin)?;
			Self::withdraw(withdrawal_data)
		}
```

**File:** modules/pallets/relayer/src/accumulate.rs (L353-368)
```rust
	pub fn accumulate_fee_and_deposit_event(
		state_machine: StateMachine,
		address: Vec<u8>,
		fee: U256,
	) {
		let _ = Fees::<T>::try_mutate(state_machine, address.clone(), |inner| {
			*inner += fee;
			Ok::<(), ()>(())
		});

		Self::deposit_event(Event::<T>::AccumulateFees {
			address: sp_runtime::BoundedVec::truncate_from(address),
			state_machine,
			amount: fee,
		});
	}
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-158)
```rust
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}

		let dispatcher = <T as Config>::IsmpHost::default();

		Nonce::<T>::try_mutate(address.clone(), withdrawal_data.dest_chain, |value| {
			*value += 1;
			Ok::<(), ()>(())
		})
		.map_err(|_| Error::<T>::ErrorCompletingCall)?;

		let beneficiary_address = withdrawal_data.beneficiary.clone().unwrap_or(address.clone());
		let (to, body) = match withdrawal_data.dest_chain {
			s if s.is_substrate() => (
				HYPERBRIDGE_MODULE_ID.to_vec(),
				Message::WithdrawRelayerFees(WithdrawalRequest {
					amount: available_amount.low_u128(),
					account: AccountId32::try_from(&beneficiary_address[..])
						.map_err(|_| Error::<T>::InvalidPublicKey)?,
				})
				.encode(),
			),
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
```

**File:** evm/src/core/EvmHost.sol (L617-621)
```text
        address oldFeeToken = feeToken();
        if (oldFeeToken != address(0) && oldFeeToken != params.feeToken) {
            uint256 balance = IERC20(oldFeeToken).balanceOf(address(this));
            if (balance != 0) revert CannotChangeFeeToken();
        }
```

**File:** evm/src/core/EvmHost.sol (L651-660)
```text
    function withdraw(WithdrawParams memory params) external restrict(_hostParams.hostManager) {
        if (params.token == address(0)) {
            // this is safe because re-entrancy is mitigated before dispatching requests
            (bool sent,) = params.beneficiary.call{value: params.amount}("");
            if (!sent) revert WithdrawalFailed();
        } else {
            IERC20(params.token).safeTransfer(params.beneficiary, params.amount);
        }
        emit HostWithdrawal({beneficiary: params.beneficiary, amount: params.amount, token: params.token});
    }
```

**File:** evm/src/core/HostManager.sol (L95-109)
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
        } else if (action == OnAcceptActions.SetHostParam) {
            HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
            IHostManager(_params.host).updateHostParams(hostParams);
        }
    }
```
