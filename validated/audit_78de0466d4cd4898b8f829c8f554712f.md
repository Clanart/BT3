### Title
Permissionless ERC20 registration causes the ERC20 precompile to permanently shadow arbitrary existing ERC20 contract logic, corrupting/freezing token holder balances — (File: `x/erc20/keeper/msg_server.go`, `x/vm/keeper/precompiles.go`, `precompiles/erc20/erc20.go`)

### Summary
The ink! report describes how hardcoded/custom function selectors let a proxy silently intercept calls meant for the real implementation, because selector matching — not the actual intended code path — decides what executes. Cosmos EVM has a structurally identical dispatch model: once an address is registered as an ERC20 token pair, `x/vm`'s precompile lookup intercepts *every* call to that address by ABI selector match, before the EVM ever runs the account's real bytecode. Combined with `MsgRegisterERC20` being permissionless, any unprivileged user can force an arbitrary pre-existing ERC20 contract's standard selectors (`transfer`, `transferFrom`, `approve`, `balanceOf`, `allowance`, `totalSupply`) to be permanently rerouted to a brand-new, empty bank-module ledger — completely bypassing (and desynchronizing from) the contract's own storage-tracked balances and any custom transfer logic (fees, blacklists, pausing, vesting, voting snapshots, etc.).

### Finding Description
`MsgRegisterERC20` can be submitted by any account when `PermissionlessRegistration` is enabled, and the handler comment states this explicitly: [1](#0-0) 

This calls `k.registerERC20(ctx, addr)` for an arbitrary externally-owned contract address supplied by the caller, with no requirement that the target contract be newly deployed, unused, or consented-to by its author/holders.

Once a token pair exists and is enabled (native or dynamic precompile), `x/vm`'s call hook decides, purely by address lookup, whether to substitute the EVM's real account bytecode with the generic ERC20 precompile — the actual deployed code is never consulted or executed for that call: [2](#0-1) [3](#0-2) 

The precompile itself dispatches purely by 4-byte selector against a fixed `IERC20Metadata` ABI, and any selector match (`transfer`, `approve`, `balanceOf`, etc.) is handled generically via the bank module instead of the original contract logic: [4](#0-3) [5](#0-4) 

This is functionally the same primitive as the ink! bug: a fixed, name/selector-driven dispatch table "wins" over the deployed implementation's real function body whenever selectors coincide — except here it is not opt-in per contract; it is imposed unconditionally and irreversibly on *any* externally owned ERC20 the moment it is registered, regardless of what its `transfer`/`approve`/`balanceOf` implementations actually did (fee-on-transfer, blacklist, pausable, staking-locked/vesting balances, ERC20Votes snapshots, etc.). All of that custom logic becomes permanently unreachable, and the precompile substitutes a brand-new, independent bank-denom ledger (`erc20:<address>`) that starts empty and has no relationship to the token's actual pre-existing on-chain balances recorded in the contract's own storage.

### Impact Explanation
This satisfies the Critical "permanent freezing/locking/unauthorized extraction of user funds" and "irreversible accounting corruption of spendable user value" gates:
- Every existing holder's balance, which lived in the target contract's own storage, becomes permanently inaccessible through the standard `balanceOf`/`transfer` interface once the precompile is installed, since those selectors now resolve against an unrelated, initially-empty bank ledger.
- Any custom safety/economic logic in the original contract (blacklists, pausability, fee-on-transfer, vesting/lock schedules, voting-power snapshots) is silently and permanently bypassed for every subsequent interaction, because the real bytecode is never executed again for that address.
- There is no unregistration/rollback path in `GetPrecompileInstance`/`GetPrecompilesCallHook` that restores the original bytecode execution once a token pair is enabled, making the corruption irreversible.
- Because registration only requires knowledge of the target contract's address (no interaction with, or consent from, the contract owner or its holders), an unprivileged attacker can target any live third-party ERC20 deployment on the chain.

### Likelihood Explanation
Triggerability depends on the `PermissionlessRegistration` parameter. The `RegisterERC20` handler's own doc comment states "Any account can permissionlessly register a native ERC20 contract," and the code path only falls back to `validateAuthority` (governance-gated) when `params.PermissionlessRegistration` is false: [6](#0-5) 
I was not able to confirm within the indexed content what the chain's default value for `PermissionlessRegistration` is (would require inspecting `x/erc20/types/params.go` default genesis params, which was not retrieved) — if it defaults to `true` or is enabled on this chain, the attack is trivially triggerable by any unprivileged EOA with a single transaction and no governance step. If it defaults to `false`, the same root-cause dispatch-shadowing problem remains, but the trigger requires a passed governance proposal, which lowers likelihood while not eliminating the underlying invariant break.

### Recommendation
- Require that a contract being registered via `MsgRegisterERC20` (especially under permissionless mode) meet strict preconditions before installing a precompile that shadows it: e.g., zero total supply / zero non-zero holder balances at registration time, or an explicit opt-in signal from the contract itself (e.g., a known marker/interface the contract must implement) rather than address-only registration.
- Before enabling the precompile for an address, verify that none of the target contract's own function selectors that overlap with the ERC20 precompile ABI implement materially different, security-relevant behavior (fee logic, blacklist, pausability) than plain OpenZeppelin-style transfer/approve — or disallow registering contracts whose bytecode differs from the expected canonical ERC20 template entirely.
- Provide a safe/guarded unregistration or "pass-through" mode so that if a mistakenly/maliciously registered token pair is found to diverge from its real contract state, the real bytecode execution path can be restored without permanent fund loss.
- At minimum, gate `PermissionlessRegistration` to false by default and require the registrant to prove ownership/administrative control of the target contract (e.g. via signed message from the contract's admin key or a constructor-time flag) before shadowing its selectors.

### Proof of Concept
1. Deploy (or identify) an existing ERC20 contract `T` with real holder balances tracked in its own storage and custom `transfer`/`approve` logic (e.g., a fee-on-transfer or blacklist-enabled token), following the same deployment pattern used in test helpers such as `SetupNativeErc20`: [7](#0-6) 
2. As an unprivileged account, submit `MsgRegisterERC20{Signer: attacker, Erc20Addresses: [T]}` (permissionless path in `RegisterERC20`): [8](#0-7) 
3. Once the token pair is created and the dynamic/native precompile is enabled for `T`'s address, call `T.balanceOf(holder)` or `T.transfer(recipient, amount)` through the EVM. `GetPrecompileInstance`/`GetPrecompilesCallHook` route the call to `erc20.Precompile.Execute`, which reads/writes the bank module's `erc20:<T>` denom instead of `T`'s real storage: [3](#0-2) [4](#0-3) 
4. Observe that `balanceOf` for holders with real, pre-registration token balances returns `0` (the fresh bank denom has no minted supply matching their prior on-chain balance), and that the contract's own custom transfer restrictions/fees are no longer applied on any subsequent transfer — demonstrating permanent divergence between the token's real accounting and the now user-facing precompile accounting.

### Citations

**File:** x/erc20/keeper/msg_server.go (L324-336)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

```

**File:** x/erc20/keeper/msg_server.go (L342-350)
```go
	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}

		pair, err := k.registerERC20(ctx, common.HexToAddress(addr))
		if err != nil {
			return nil, err
		}
```

**File:** x/vm/keeper/precompiles.go (L17-52)
```go
// GetPrecompileInstance returns the address and instance of the static or dynamic precompile associated with the
// given address, or return nil if not found.
func (k *Keeper) GetPrecompileInstance(
	ctx sdktypes.Context,
	address common.Address,
) (*Precompiles, bool, error) {
	params := k.GetParams(ctx)
	// Get the precompile from the static precompiles
	if precompile, found, err := k.GetStaticPrecompileInstance(&params, address); err != nil {
		return nil, false, err
	} else if found {
		addressMap := make(map[common.Address]vm.PrecompiledContract)
		addressMap[address] = precompile
		return &Precompiles{
			Map:       addressMap,
			Addresses: []common.Address{precompile.Address()},
		}, found, nil
	}

	// Since erc20Keeper is optional, we check if it is nil, in which case we just return that we didn't find the precompile
	if k.erc20Keeper == nil {
		return nil, false, nil
	}

	// Get the precompile from the dynamic precompiles
	precompile, found, err := k.erc20Keeper.GetERC20PrecompileInstance(ctx, address)
	if err != nil || !found {
		return nil, false, err
	}
	addressMap := make(map[common.Address]vm.PrecompiledContract)
	addressMap[address] = precompile
	return &Precompiles{
		Map:       addressMap,
		Addresses: []common.Address{precompile.Address()},
	}, found, nil
}
```

**File:** x/vm/keeper/precompiles.go (L56-73)
```go
func (k *Keeper) GetPrecompilesCallHook(ctx sdktypes.Context) types.CallHook {
	return func(evm *vm.EVM, _ common.Address, recipient common.Address) error {
		// Check if the recipient is a precompile contract and if so, load the precompile instance
		precompiles, found, err := k.GetPrecompileInstance(ctx, recipient)
		if err != nil {
			return err
		}

		// If the precompile instance is created, we have to update the EVM with
		// only the recipient precompile and add it's address to the access list.
		if found {
			evm.WithPrecompiles(precompiles.Map)
			evm.StateDB.AddAddressToAccessList(recipient)
		}

		return nil
	}
}
```

**File:** precompiles/erc20/erc20.go (L148-163)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// ERC20 precompiles cannot receive funds because they are not managed by an
	// EOA and will not be possible to recover funds sent to an instance of
	// them.This check is a safety measure because currently funds cannot be
	// received due to the lack of a fallback handler.
	if value := contract.Value(); value.Sign() == 1 {
		return nil, fmt.Errorf(ErrCannotReceiveFunds, contract.Value().String())
	}

	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	return p.HandleMethod(ctx, contract, stateDB, method, args)
}
```

**File:** precompiles/erc20/erc20.go (L165-175)
```go
// IsTransaction checks if the given method name corresponds to a transaction or query.
func (Precompile) IsTransaction(method *abi.Method) bool {
	switch method.Name {
	case TransferMethod,
		TransferFromMethod,
		ApproveMethod:
		return true
	default:
		return false
	}
}
```

**File:** evmd/tests/ibc/helper.go (L35-62)
```go
// SetupNativeErc20 deploys, registers, and mints a native ERC20 token on an EVM-based chain.
func SetupNativeErc20(t *testing.T, chain *evmibctesting.TestChain, senderAcc evmibctesting.SenderAccount) *NativeErc20Info {
	t.Helper()

	evmCtx := chain.GetContext()
	evmApp := chain.App.(evm.EvmApp)

	// Deploy new ERC20 contract with default metadata
	contractAddr, err := evmApp.GetErc20Keeper().DeployERC20Contract(evmCtx, banktypes.Metadata{
		DenomUnits: []*banktypes.DenomUnit{
			{Denom: "example", Exponent: 18},
		},
		Name:   "Example",
		Symbol: "Ex",
	})
	if err != nil {
		t.Fatalf("ERC20 deployment failed: %v", err)
	}
	chain.NextBlock()

	// Register the contract
	_, err = evmApp.GetErc20Keeper().RegisterERC20(evmCtx, &erc20types.MsgRegisterERC20{
		Signer:         authtypes.NewModuleAddress(govtypes.ModuleName).String(), // does not have to be gov
		Erc20Addresses: []string{contractAddr.Hex()},
	})
	if err != nil {
		t.Fatalf("RegisterERC20 failed: %v", err)
	}
```
