This confirms the vulnerability path: any pallet on Hyperbridge can freely craft a `DispatchPost` with an arbitrary `from` field (any module id it chooses, not restricted to a canonical "governance" identity) and `to` set to any EVM contract address [1](#0-0) [2](#0-1) . The `source` field is set purely from `self.host_state_machine()`, i.e. it is always the Hyperbridge chain id for *any* request dispatched by *any* module/pallet/user extrinsic on that chain [3](#0-2) . Example pallets (`pallet_ismp_demo`, the docs sample) show ordinary signed extrinsics constructing a `DispatchPost` with attacker-controlled `to` (arbitrary destination module bytes) reaching `IsmpDispatcher::dispatch_request` [4](#0-3) .

### Title
Governance-only Intent Gateway actions (`NewDeployment`/`UpdateParams`/`SweepDust`/`UpgradeContract`) are authenticated only by source chain-id, not by sending module — ([File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
`ExtrinsicIntents.onAccept` gates its governance-only actions (register a new gateway instance, change protocol params, sweep dust, upgrade the proxy implementation) with a check that only verifies `incoming.request.source == hyperbridge`, without verifying `incoming.request.from` (the specific module/account on Hyperbridge that sent the message). Because Hyperbridge (the coprocessor parachain) allows any pallet/account to dispatch an ISMP `PostRequest` with an arbitrary `to` address and freely chosen `from` module id, any unprivileged actor able to submit an extrinsic that reaches `IsmpDispatcher::dispatch_request` can forge a message that looks, to the `IntentGatewayV2`/`ExtrinsicIntents` contract, indistinguishable from a genuine governance dispatch.

### Finding Description
`ExtrinsicIntents.onAccept` splits handling into two authentication tiers:

- `RedeemEscrow` / `RefundEscrow`: authenticated via `_authenticate`, which checks both `request.source` and that `request.from` equals the registered peer gateway address for that chain [5](#0-4) .
- `NewDeployment` / `UpdateParams` / `SweepDust` / `UpgradeContract`: authenticated only by `keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())` — the `request.from` field (the specific pallet/account on Hyperbridge that dispatched the message) is never checked [6](#0-5) .

On the Hyperbridge (Nexus/Gargantua) side, `source` for every outgoing `PostRequest` is unconditionally set to `self.host_state_machine()` regardless of who dispatched it [2](#0-1) , and `to`/`from`/`body` are fully attacker-controlled parameters passed into `dispatch_request` by ordinary signed extrinsics [4](#0-3) , [7](#0-6) . There is no protocol-level restriction (comparable to `_authenticate`'s peer-address check) tying `source == hyperbridge` to a specific privileged sender module — "coming from Hyperbridge" only means "any account able to submit an extrinsic on the Hyperbridge chain."

This is the direct structural analog of the external report: an access-control check based on a coarse identity ("registered chain") rather than the exact expected principal ("registered module/governance address"), letting an unprivileged party masquerade as the trusted party and reach privileged operations — just as a compromised `AddressResolver` mapping let an attacker-controlled address pass `onlyAcceptedContracts()`.

### Impact Explanation
If reachable, this breaks: unauthorized execution of governance-only intent-gateway operations. `SweepDust` transfers arbitrary attacker-chosen amounts of protocol-held tokens to an attacker-chosen `beneficiary` [8](#0-7) ; `NewDeployment` lets an attacker register a malicious address as the "registered gateway" for any state machine [9](#0-8) , which subsequently makes `_authenticate` trust that malicious address for `RedeemEscrow`/`RefundEscrow` messages — cascading into theft of escrowed order funds; and `UpgradeContract` allows arbitrary implementation replacement, i.e. full contract takeover [10](#0-9) . This matches the bounty's "stealing or loss of funds", "unauthorized transaction or execution," and "false state acceptance" categories.

### Likelihood Explanation
I could not fully confirm, within the available index, whether the production Hyperbridge/Nexus router enforces an additional check that restricts which pallet/module id is allowed to appear as `from` for messages targeted at EVM `HostManager`-style governance recipients (e.g., a dedicated `HostExecutive`/governance-only dispatch path), separate from the generic `pallet-ismp` dispatch primitive shown in the demo pallet. The demo/example pallets clearly show unrestricted `from`/`to` construction, but if the real Nexus runtime's outbound dispatch for intent-gateway governance messages is restricted to a single privileged pallet (analogous to `HostExecutive`), then this path would not be exploitable by an ordinary user. This uncertainty should be resolved by inspecting the actual runtime code that dispatches `NewDeployment`/`UpdateParams`/`SweepDust`/`UpgradeContract` messages to `IntentGatewayV2` instances, which was not fully retrievable from the index.

### Recommendation
In `ExtrinsicIntents.onAccept`, in addition to checking `request.source == hyperbridge`, also verify `request.from` against a specific, immutable, privileged module id (e.g., the intent-gateway's designated Hyperbridge-side governance pallet/account), mirroring the same-principal check already used in `_authenticate`. This closes the gap between "any sender on the trusted chain" and "the specific trusted sender."

### Proof of Concept
Not independently confirmed end-to-end due to inability to verify the real Nexus governance-dispatch restrictions in this session; the local code-level gap is demonstrated by:
1. `evm/src/apps/intentsv2/ExtrinsicIntents.sol:296-309` — governance branch checks only `request.source`.
2. `modules/pallets/ismp/src/dispatcher.rs:128-146` — `source` is set to the local chain id regardless of caller, `from`/`to`/`body` are caller-supplied.
3. `modules/pallets/demo/src/lib.rs:216-239` — example of an ordinary signed extrinsic freely setting `to` to any EVM address, illustrating that the dispatch primitive itself imposes no restriction on `from`/`to`.

If Nexus's production ISMP router likewise exposes any general-purpose dispatch entrypoint to intents-related destinations without pinning `from` to a specific privileged pallet, an attacker could dispatch `{source: hyperbridge_id, from: <attacker-chosen>, to: intentGatewayAddress, body: [SweepDust|NewDeployment|UpgradeContract] payload}` and have it accepted by `onAccept`.

### Citations

**File:** docs/content/developers/polkadot/dispatching.mdx (L15-43)
```text
```rust showLineNumbers
pub struct DispatchPost {
    pub dest: StateMachine,
    pub from: Vec<u8>,
    pub to: Vec<u8>,
    pub timeout: u64,
    pub body: Vec<u8>,
}

struct FeeMetadata<T> {
    pub payer: <T as Config>::AccountId,
    pub fee: <T as Config>::Balance,
}

pub enum DispatchRequest {
    Post(DispatchPost),
    Get(DispatchGet),
}

trait IsmpDispatcher  {
    fn dispatch_request(
        &self,
        request: DispatchRequest,
        fee: FeeMetadata<T>,
    ) -> Result<H256, Error>;

    // ...
}
```
```

**File:** docs/content/developers/polkadot/dispatching.mdx (L57-76)
```text
```rust showLineNumbers
#[pallet::weight(T::dispatch())]
#[pallet::call_index(0)]
pub fn send_message(
    origin: OriginFor<T>,
    post: DispatchPost,
    fee: T::Balance,
) -> DispatchResultWithPostInfo {
    let signer = ensure_signed(origin)?;
    let dispatcher = pallet_ismp::Pallet::<Runtime>::default();
    let commitment = dispatcher.dispatch_request(
        DispatchRequest::Post(post),
        FeeMetadata {
            payer: signer,
            fee,
        }
    )?;

    Ok(())
}
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L128-146)
```rust
			DispatchRequest::Post(dispatch_post) => {
				let post = PostRequest {
					source: self.host_state_machine(),
					dest: dispatch_post.dest,
					nonce: self.next_nonce(),
					from: dispatch_post.from,
					to: dispatch_post.to,
					timeout_timestamp: if dispatch_post.timeout == 0 {
						0
					} else {
						<T::TimestampProvider as UnixTime>::now()
							.as_secs()
							.saturating_add(dispatch_post.timeout)
					},
					body: dispatch_post.body,
				};
				Request::Post(post)
			},
		};
```

**File:** modules/pallets/demo/src/lib.rs (L216-239)
```rust
		/// Dispatch request to a connected EVM chain.
		#[pallet::weight(Weight::from_parts(1_000_000, 0))]
		#[pallet::call_index(2)]
		pub fn dispatch_to_evm(origin: OriginFor<T>, params: EvmParams) -> DispatchResult {
			let origin = ensure_signed(origin)?;
			let post = DispatchPost {
				dest: StateMachine::Evm(params.destination),
				from: PALLET_ID.to_bytes(),
				to: params.module.0.to_vec(),
				timeout: params.timeout,
				body: b"Hello from polkadot".to_vec(),
			};
			let dispatcher = T::IsmpHost::default();
			for _ in 0..params.count {
				// dispatch the request
				dispatcher
					.dispatch_request(
						DispatchRequest::Post(post.clone()),
						FeeMetadata { payer: origin.clone(), fee: Default::default() },
					)
					.map_err(|_| Error::<T>::TransferFailed)?;
			}
			Ok(())
		}
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L296-309)
```text

        // only hyperbridge is permitted to perform these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
        } else if (kind == RequestKind.UpdateParams) {
            _updateParams(abi.decode(incoming.request.body[1:], (ParamsUpdate)));
        } else if (kind == RequestKind.SweepDust) {
            _sweepDust(abi.decode(incoming.request.body[1:], (SweepDust)));
        } else if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(incoming.request.body[1:], (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        }
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L514-524)
```text
    /**
     * @dev Registers a new IntentGateway deployment for a remote state machine.
     * Called when Hyperbridge governance adds support for a new chain. The gateway
     * address is stored in `_instances` keyed by the hash of the state machine ID.
     *
     * @param body The deployment info containing the state machine ID and gateway address.
     */
    function _addDeployment(Deployment memory body) internal {
        _instances[keccak256(body.chain)] = body.gateway;
        emit DeploymentAdded({chain: string(body.chain), gateway: body.gateway});
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L570-597)
```text
    /**
     * @dev Transfers accumulated protocol dust (surplus tokens) to a specified beneficiary.
     * Called by Hyperbridge governance to sweep protocol-owned tokens that have accumulated
     * from fees, surplus splits, and calldata execution residuals.
     *
     * Supports both native tokens and ERC-20 tokens.
     *
     * @param req The sweep request containing the beneficiary address and token amounts.
     */
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
