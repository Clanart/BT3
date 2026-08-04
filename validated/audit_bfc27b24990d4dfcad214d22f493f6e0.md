Based on the evidence gathered, I found a real analog: `pallet_ismp_demo`'s `on_accept` mints native funds based on an inbound cross-chain payload without verifying `request.from` (the sending module/contract address on the source chain) at all, unlike every other production ISMP app in this codebase (`HyperFungibleToken`, `WrappedHyperFungibleToken`, `pallet_hyper_fungible_token`) which explicitly check `request.from`/`request.source` against a registered peer address before crediting value.

### Title
Unauthenticated cross-chain mint in `pallet_ismp_demo::on_accept` — no verification of sending module/contract - (File: `modules/pallets/demo/src/lib.rs`)

### Summary
`pallet_ismp_demo` is wired into the `gargantua` runtime's ISMP router (`parachain/runtimes/gargantua/src/ismp.rs`, `ProxyModule::on_accept` routes `to == pallet_ismp_demo::PALLET_ID` to it) and mints real native balance to an attacker-chosen account for any inbound `Polkadot`/`Kusama`-sourced POST request, without checking who sent it. This is the structural analog of the reported `postMessage(..., '*')` bug: the message-acceptance boundary trusts the payload's content instead of restricting it to a known, trusted counterpart.

### Finding Description
In `modules/pallets/demo/src/lib.rs`, `IsmpModule::on_accept` for `IsmpModuleCallback<T>` is: [1](#0-0) 

It only branches on `source_chain` (`StateMachine::Evm(_)` vs `Polkadot`/`Kusama`) and, for the Substrate branch, decodes an arbitrary `Payload { to, amount, from }` from the request body and directly calls `NativeCurrency::mint_into(&payload.to, payload.amount)` — crediting **new native balance** to whatever account `payload.to` specifies, for whatever `payload.amount` the attacker encodes. There is no check of `request.from` (which module/pallet on the source chain actually dispatched the message) and no check that the message corresponds to any prior lock/burn on that source chain.

Compare this to every other production cross-chain value-transfer app in the same repo, which authenticates the counterpart before moving value:
- `HyperFungibleToken.onAccept` (Solidity): `if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();` [2](#0-1) 
- `pallet_hyper_fungible_token::on_accept` (Substrate): `let local_asset_id = ContractToAsset::<T>::get(source, &from).ok_or(HftError::UnknownSourceContract(source))?;` [3](#0-2) 

`pallet_ismp_demo` has neither an `expectedSource`/`ContractToAsset`-style allowlist nor any use of the `from` field in its Substrate mint path — it is reachable via the `ProxyModule::on_accept` router in `parachain/runtimes/gargantua/src/ismp.rs`, which dispatches solely based on the `to` module ID and does not itself authenticate `from` for this pallet: [4](#0-3) 

### Impact Explanation
Any account on any connected `Polkadot`/`Kusama` state machine (or, once ISMP is bidirectional, any party able to get a POST request delivered to this chain with `to = pallet_ismp_demo::PALLET_ID`) can mint arbitrary amounts of the chain's native currency to an arbitrary beneficiary, with no corresponding burn/lock ever happening on the source side. This is unauthenticated fund creation/theft — it matches the bounty's "stealing or loss of funds" / "unauthorized execution" categories directly, since it lets an unprivileged actor fabricate value out of thin air by simply getting any POST request accepted by this module.

### Likelihood Explanation
Likelihood depends on whether `pallet_ismp_demo` is deployed with real value backing (i.e., `T::NativeCurrency` bound to the chain's real `Balances` pallet) on a live, value-bearing `gargantua` deployment rather than purely as a demo/testnet fixture — I could not fully confirm the concrete `NativeCurrency` type binding for `pallet_ismp_demo::Config` in the runtime within the available tool budget. If it is bound to the real native balance and the pallet is reachable via a live ISMP connection to another chain, exploitation requires only dispatching a standard cross-chain POST request — no privileged role, relayer collusion, or governance action needed, making likelihood high conditional on that deployment fact.

### Recommendation
Add the same source-authentication pattern used by `pallet_hyper_fungible_token` and the Solidity `HyperFungibleToken`/`WrappedHyperFungibleToken` contracts: require `request.from` to match a pre-registered, trusted peer module/contract address per `source` chain (e.g., a `ContractToAsset`/`SupportedChains`-style map) before minting or transferring any value in `pallet_ismp_demo::on_accept`. If this pallet is intended purely as a non-value-bearing example, it should not be wired into any runtime whose `NativeCurrency` implementation controls real, tradable balance, or it should be feature-gated out of production runtime builds.

### Proof of Concept
1. From any chain configured as an ISMP counterparty to the `gargantua` runtime, dispatch a POST request with `to = pallet_ismp_demo::PALLET_ID` (`b"ismp-ast"`), `source = StateMachine::Polkadot(_)` or `Kusama(_)`, and body encoding `Payload { to: <attacker_account>, amount: <large_amount>, from: <anything> }`.
2. Once relayed and accepted by Hyperbridge's standard request-handling pipeline (`modules/ismp/core/src/handlers/request.rs`, which only checks destination/timeout/duplicate/proxy — not application-level sender identity), `ProxyModule::on_accept` in `parachain/runtimes/gargantua/src/ismp.rs` routes it to `pallet_ismp_demo::IsmpModuleCallback::on_accept`.
3. `on_accept` decodes `Payload` and calls `NativeCurrency::mint_into(&payload.to, payload.amount)` unconditionally — crediting the attacker's account with `amount` of native balance, with no verification that `request.from` is any authorized minting counterpart. [5](#0-4)

### Citations

**File:** modules/pallets/demo/src/lib.rs (L368-399)
```rust
impl<T: Config> IsmpModule for IsmpModuleCallback<T> {
	fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
		let source_chain = request.source;

		match source_chain {
			StateMachine::Evm(_) => Pallet::<T>::deposit_event(Event::Request {
				source: source_chain,
				data: unsafe { String::from_utf8_unchecked(request.body) },
			}),
			StateMachine::Polkadot(_) | StateMachine::Kusama(_) => {
				let payload =
					<Payload<T::AccountId, <T as Config>::Balance> as codec::Decode>::decode(
						&mut &*request.body,
					)
					.map_err(|_| IsmpError::Custom("Failed to decode request data".to_string()))?;
				<T::NativeCurrency as Mutate<T::AccountId>>::mint_into(
					&payload.to,
					payload.amount.into(),
				)
				.map_err(|_| IsmpError::Custom("Failed to mint funds".to_string()))?;
				Pallet::<T>::deposit_event(Event::<T>::BalanceReceived {
					from: payload.from,
					to: payload.to,
					amount: payload.amount,
					source_chain,
				});
			},
			source => Err(IsmpError::Custom(format!("Unsupported source {source:?}")))?,
		}

		Ok(weight())
	}
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-300)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        Message memory message = abi.decode(request.body, (Message));
        address beneficiary = _toAddr(message.to);
        _mint(beneficiary, message.amount);
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L50-56)
```rust
	fn on_accept(
		&self,
		PostRequest { body, from, source, .. }: PostRequest,
	) -> Result<Weight, anyhow::Error> {
		// Authenticate: look up which local asset this contract address maps to
		let local_asset_id = ContractToAsset::<T>::get(source, &from)
			.ok_or(HftError::UnknownSourceContract(source))?;
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L398-422)
```rust
		if request.dest != HostStateMachine::get() {
			Ismp::dispatch_request(
				Request::Post(request),
				FeeMetadata::<Runtime> { payer: [0u8; 32].into(), fee: Default::default() },
			)?;
			return Ok(Weight::from_parts(0, 0));
		}

		let pallet_id =
			ModuleId::from_bytes(&request.to).map_err(|err| Error::Custom(err.to_string()))?;

		match pallet_id {
			pallet_ismp_demo::PALLET_ID =>
				pallet_ismp_demo::IsmpModuleCallback::<Runtime>::default().on_accept(request),

			#[cfg(not(feature = "no-bandwidth"))]
			id if id == ModuleId::Pallet(pallet_bandwidth::pallet::PALLET_BANDWIDTH) =>
				pallet_bandwidth::Pallet::<Runtime>::default().on_accept(request),

			pallet_hyper_fungible_token::PALLET_ID =>
				pallet_hyper_fungible_token::Pallet::<Runtime>::default().on_accept(request),

			_ => Err(anyhow!("Destination module not found")),
		}
	}
```
