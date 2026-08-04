## Finding Confirmed

The reported semantic gap is real, and it's actually more severe than the question suggests: `state_machine()` doesn't even inspect the stored `bool` value — it only checks storage-key *presence* via `contains_key`.

### Title
Disabling a state machine via `set_supported_state_machine(id, false)` has no effect — `state_machine()` uses `contains_key` instead of reading the stored flag - (File: `modules/ismp/clients/tendermint/src/lib.rs`, `modules/ismp/clients/ismp-arbitrum/src/lib.rs`)

### Summary
`SupportedStateMachines` is a `StorageMap<StateMachine, bool>` [1](#0-0) . The admin-gated calls are:
- `set_supported_state_machine(sm, supported)` → always **inserts** the key with whatever boolean is passed [2](#0-1) 
- `remove_supported_state_machine(sm)` → **removes** the key entirely [3](#0-2) 

However, the consumer of this storage, `TendermintClient::state_machine()`, checks only key existence, not the value:
```rust
if crate::pallet::SupportedStateMachines::<T>::contains_key(id) {
    ...Ok(...)
} else {
    Err(...)
}
``` [4](#0-3) 

`StorageMap::contains_key` returns `true` for any inserted entry regardless of its value (`true` or `false`). So an admin calling `set_supported_state_machine(sm, false)` — intending to disable `sm` — actually leaves it fully "supported" from `state_machine()`'s perspective, because the key still exists. Only `remove_supported_state_machine` (which deletes the key) actually disables routing/verification for that chain.

The same `contains_key`-only pattern (ignoring the stored bool) is used in `ismp-arbitrum`'s `ArbitrumConsensusClient::state_machine()` [5](#0-4) , and grep results indicate the same helper/import pattern in `ismp-optimism`, suggesting this is a systemic issue across the EVM-style consensus clients built on this shared pallet pattern, not a one-off typo.

### Finding Description
`state_machine()` is the gate that determines whether the ISMP host will construct a `StateMachineClient` (and thus accept/route/verify requests, responses, and timeouts) for a given `StateMachine` id. The pallet's public API exposes two semantically distinct disable mechanisms (`set_supported_state_machine(id, false)` vs `remove_supported_state_machine(id)`), but the enforcement code only implements one of them correctly. An operator who uses the documented/expected toggle (`set_supported_state_machine(id, false)`) — which is the natural, non-destructive way to disable a chain while preserving audit history/event trail — will not actually disable anything. Any subsequent unprivileged proof/message submission targeting that "disabled" chain still passes the `state_machine()` gate and proceeds into consensus/state verification as if the chain were fully supported.

### Impact Explanation
This causes false state/route acceptance: a chain state machine the operator believes is cut off (e.g., due to detected compromise, chain halt, or a decommission decision) continues to accept and settle in-flight requests, responses, and timeouts. This can result in unauthorized execution or fund movement against a chain that was supposed to be excluded from trust, undermining the operator's security control and any incident-response action taken via `set_supported_state_machine(false)`.

### Likelihood Explanation
High, in the sense that it is deterministic and always triggers — there is no privileged intervention needed on the attacker side beyond normal, expected relayer/user activity (submitting a proof for a pending request/timeout). The only precondition is that an admin used `set_supported_state_machine(id, false)` rather than `remove_supported_state_machine(id)` to disable a chain, which is a plausible/likely operational choice since both calls are presented as equivalent "disable" mechanisms and the event emitted (`StateMachineSupportUpdated { supported: false }`) is identical in both cases, masking the difference from operators monitoring events.

### Recommendation
Change `state_machine()` in `modules/ismp/clients/tendermint/src/lib.rs` (and the analogous implementations in `ismp-arbitrum`, `ismp-optimism`, etc.) to check the stored boolean value, e.g. `SupportedStateMachines::<T>::get(id) == Some(true)`, instead of `contains_key`. Alternatively, remove the two-call API entirely and have `set_supported_state_machine(id, false)` also call `remove` internally, or have `remove_supported_state_machine` be the sole disable path with `set_supported_state_machine` only ever inserting `true`.

### Proof of Concept
1. Admin calls `set_supported_state_machine(StateMachine::Evm(X), false)`. Storage: `SupportedStateMachines[Evm(X)] = Some(false)`.
2. A relayer/user submits a pending cross-chain request/response/timeout proof targeting `Evm(X)`.
3. ISMP host calls `TendermintClient::state_machine(Evm(X))`.
4. `SupportedStateMachines::<T>::contains_key(Evm(X))` returns `true` (key exists, value irrelevant) → `Ok(Box::new(TendermintEvmStateMachine::default()))` is returned.
5. The message proceeds to be verified/settled, contradicting the admin's intent to disable `Evm(X)`.
6. Contrast: if the admin instead calls `remove_supported_state_machine(Evm(X))`, step 4's `contains_key` returns `false`, and settlement is correctly rejected — demonstrating the semantic gap directly causes divergent, unsafe behavior for what operators reasonably expect to be equivalent "disable" actions.

### Citations

**File:** modules/ismp/clients/tendermint/src/pallet.rs (L32-35)
```rust
	#[pallet::storage]
	#[pallet::getter(fn supported_state_machines)]
	pub type SupportedStateMachines<T: Config> =
		StorageMap<_, Twox64Concat, StateMachine, bool, OptionQuery>;
```

**File:** modules/ismp/clients/tendermint/src/pallet.rs (L54-67)
```rust
		pub fn set_supported_state_machine(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			supported: bool,
		) -> DispatchResult {
			<T as Config>::AdminOrigin::ensure_origin(origin)?;

			SupportedStateMachines::<T>::insert(state_machine, supported);
			Self::deposit_event(Event::<T>::StateMachineSupportUpdated {
				state_machine,
				supported,
			});
			Ok(())
		}
```

**File:** modules/ismp/clients/tendermint/src/pallet.rs (L72-84)
```rust
		pub fn remove_supported_state_machine(
			origin: OriginFor<T>,
			state_machine: StateMachine,
		) -> DispatchResult {
			<T as Config>::AdminOrigin::ensure_origin(origin)?;

			SupportedStateMachines::<T>::remove(state_machine);
			Self::deposit_event(Event::<T>::StateMachineSupportUpdated {
				state_machine,
				supported: false,
			});
			Ok(())
		}
```

**File:** modules/ismp/clients/tendermint/src/lib.rs (L199-208)
```rust
	fn state_machine(&self, id: StateMachine) -> Result<Box<dyn StateMachineClient>, Error> {
		if crate::pallet::SupportedStateMachines::<T>::contains_key(id) {
			match id {
				StateMachine::Evm(_) => Ok(Box::new(TendermintEvmStateMachine::<H, T>::default())),
				_ => Err(Error::Custom("Unsupported state machine or chain ID".to_string())),
			}
		} else {
			Err(Error::Custom(alloc::format!("State machine not supported: {id:?}")))
		}
	}
```

**File:** modules/ismp/clients/ismp-arbitrum/src/lib.rs (L236-242)
```rust
	fn state_machine(&self, id: StateMachine) -> Result<Box<dyn StateMachineClient>, Error> {
		if SupportedStateMachines::<T>::contains_key(id) {
			Ok(Box::new(<EvmStateMachine<H, T>>::default()))
		} else {
			Err(ArbitrumError::UnsupportedStateMachine(id).into())
		}
	}
```
