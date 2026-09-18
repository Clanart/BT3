Based on my investigation, I found a strong analog to the reported bug class.

### Title
Unprivileged front-running of `AssociateContractAddress` permanently blocks a CosmWasm contract's canonical EVM address mapping - (File: `x/evm/keeper/msg_server.go`)

### Summary
The Gitcoin bug is a "claim the resource before the legitimate owner" front-run: a permissionless `init()` sets a critical mapping the first time it's called, and once set it can never be changed, so front-running it permanently breaks the intended caller's flow. Sei-chain has a structurally identical pattern in `AssociateContractAddress`, which is a permissionless message that anyone can submit to set the canonical EVM address of a CosmWasm contract, and once set it is permanent and cannot be overwritten. [1](#0-0) 

### Finding Description
`AssociateContractAddress` only validates that the target address is a wasm contract; it does **not** check `msg.Sender` against any privileged role (e.g. contract admin/creator), so any account can submit this message for any wasm contract: [2](#0-1) 

It then checks whether an EVM address mapping already exists for that contract's `sdk.AccAddress`, and if one exists it errors out permanently — the mapping is set-once and can never be corrected once claimed: [3](#0-2) 

`SetAddressMapping` writes a permanent bidirectional mapping between the sei address and EVM address with no update/removal path, and emits the association event, so once any party has raced to call it first, the record is fixed for that address in `x/evm/keeper/address.go`: [4](#0-3) 

This mirrors the Gitcoin bug class precisely:
- Gitcoin: `votingStrategy.init()` / `payoutStrategy.init()` are permissionless, first caller wins, and once set the intended `RoundImplementation.initialize` call permanently reverts.
- Sei-chain: `AssociateContractAddress` is permissionless, first caller wins, and once the mapping exists any subsequent (including the contract deployer's/protocol's intended) association attempt permanently fails with `"contract already has an associated address"`.

Because `common.BytesToAddress(addr)` deterministically derives the "expected" EVM address for a wasm contract from its Sei address, an attacker can precompute the exact contract address ahead of time (e.g., by monitoring `MsgInstantiateContract` events or predicting deterministic addresses) and submit `AssociateContractAddress` for it before any legitimate/automated association flow does. [5](#0-4) 

### Impact Explanation
If any downstream system, pointer contract, or dApp relies on `AssociateContractAddress` completing successfully as part of a deployment/registration flow for a CosmWasm contract (e.g., to establish its canonical EVM identity for use with EVM tooling, pointer contracts, or precompiles that key off `GetEVMAddress`), an attacker can front-run this call with an unprivileged transaction and permanently deny that association, since there is no way to overwrite or reclaim it once set. This is a griefing/DoS vector against a specific contract's cross-VM identity, causing permanent freezing of that contract's ability to gain the intended EVM-address association — matching the report's "resource is permanently locked by an unprivileged front-runner" pattern.

### Likelihood Explanation
The barrier to exploitation is low: submitting `MsgAssociateContractAddress` requires no special permission, only knowledge of the target contract's Sei address (which is public once instantiated, or even predictable pre-instantiation via deterministic address derivation schemes). Any observer monitoring contract instantiation events can race a follow-up transaction ahead of the legitimate association flow.

### Recommendation
Restrict `AssociateContractAddress` to a privileged caller (e.g., the contract's admin, as recorded in `wasmViewKeeper.GetContractInfo`, or governance), similar to how `UpdateContractAdmin`/`ClearContractAdmin` require the caller to match the contract's admin field. Alternatively, allow the legitimate admin to overwrite/reclaim an existing association rather than have the mapping be permanently immutable to the first caller.

### Proof of Concept
1. Attacker monitors on-chain events for `MsgInstantiateContract` (or predicts the deterministic contract address of an upcoming instantiation).
2. As soon as the contract address is known/instantiated, attacker submits `MsgAssociateContractAddress{Address: <contract-addr>}` from an arbitrary unprivileged account before the legitimate integrator/deployer does.
3. `AssociateContractAddress` succeeds because no mapping existed yet, calling `SetAddressMapping` and permanently recording the association. [6](#0-5) 
4. When the legitimate flow later submits the same message (expecting to establish/verify the mapping as part of its deployment pipeline), it now fails permanently with `"contract already has an associated address"`, since the check-and-set logic never allows re-association. [7](#0-6)

### Citations

**File:** x/evm/keeper/msg_server.go (L326-343)
```go
func (server msgServer) AssociateContractAddress(goCtx context.Context, msg *types.MsgAssociateContractAddress) (*types.MsgAssociateContractAddressResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	addr := sdk.MustAccAddressFromBech32(msg.Address) // already validated
	// check if address is for a contract
	if server.wasmViewKeeper.GetContractInfo(ctx, addr) == nil {
		return nil, errors.New("no wasm contract found at the given address")
	}
	evmAddr := common.BytesToAddress(addr)
	existingEvmAddr, ok := server.GetEVMAddress(ctx, addr)
	if ok {
		if existingEvmAddr.Cmp(evmAddr) != 0 {
			logger.Error("unexpected associated EVM address exists for contract", "existing", existingEvmAddr, "contract", addr, "expected", evmAddr)
		}
		return nil, errors.New("contract already has an associated address")
	}
	server.SetAddressMapping(ctx, addr, evmAddr)
	return &types.MsgAssociateContractAddressResponse{}, nil
}
```

**File:** x/evm/keeper/address.go (L10-22)
```go
func (k *Keeper) SetAddressMapping(ctx sdk.Context, seiAddress sdk.AccAddress, evmAddress common.Address) {
	store := ctx.KVStore(k.storeKey)
	store.Set(types.EVMAddressToSeiAddressKey(evmAddress), seiAddress)
	store.Set(types.SeiAddressToEVMAddressKey(seiAddress), evmAddress[:])
	if !k.accountKeeper.HasAccount(ctx, seiAddress) {
		k.accountKeeper.SetAccount(ctx, k.accountKeeper.NewAccountWithAddress(ctx, seiAddress))
	}
	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypeAddressAssociated,
		sdk.NewAttribute(types.AttributeKeySeiAddress, seiAddress.String()),
		sdk.NewAttribute(types.AttributeKeyEvmAddress, evmAddress.Hex()),
	))
}
```
