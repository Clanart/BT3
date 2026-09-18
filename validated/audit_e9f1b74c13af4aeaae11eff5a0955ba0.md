### Title
Public `addNativePointer`/`addCW20Pointer` re-registration silently mutates already-deployed ERC20 pointer's `name`/`symbol`/`decimals` in place, breaking integrator assumptions of ERC20 metadata immutability - (File: `x/evm/keeper/pointer_upgrade.go`, `precompiles/pointer/pointer.go`, `contracts/src/NativeSeiTokensERC20.sol`)

### Summary
The `pointer` precompile's `addNativePointer`/`addCW20Pointer` methods are callable by any unprivileged EVM caller [1](#0-0) , and internally call `UpsertERCNativePointer`/`UpsertERCCW20Pointer`, which re-deploy new bytecode at the **same, already-integrated** pointer contract address whenever the underlying denom metadata or CW20 `token_info` has changed [2](#0-1) . Because `name`, `symbol`, and `decimals` are baked into the pointer contract as constructor-time immutables [3](#0-2) , this upsert mechanism causes the pointer's declared ERC20 metadata to change during its lifetime — exactly the scenario described in the external yToken report — even though nothing about the pointer's on-chain address changes, so downstream integrators have no signal to re-fetch it.

### Finding Description
- `AddNative`/`AddCW20` fetch current `name`/`symbol`/`decimals` from bank denom metadata or CW20 `token_info` at call time [4](#0-3) , and pass them into `UpsertERCNativePointer`/`UpsertERCCW20Pointer`.
- `UpsertERCPointer` checks if a pointer already exists for the token/denom; if it does, instead of failing, it calls `evm.GetDeploymentCode` with the **new** metadata args and overwrites the contract's bytecode in place via `k.SetCode(writeCtx, contractAddr, ret)` — the pointer's address is unchanged [5](#0-4) .
- The pointer contract (e.g. `NativeSeiTokensERC20.sol`) hard-codes `name`, `symbol`, and `decimals` as state set once in the constructor and returned by `name()`/`symbol()`/`decimals()` view functions [6](#0-5) . When the bytecode is replaced, any new deployment-args encode different values, but the contract's *address* used by third-party integrators never changes.
- Underlying denom metadata can legitimately change: a tokenfactory denom admin can call `MsgSetDenomMetadata` at any time to update `Name`, `Symbol`, and `DenomUnits[].Exponent` (which the pointer path maps to `decimals`) with only an admin-authorization check, no restriction on whether an ERC20 pointer already exists for that denom [7](#0-6) . Likewise a CW20 contract's `token_info` (name/symbol) can change via contract migration/governance.
- After such a change, **any unprivileged caller** can invoke `addNativePointer`/`addCW20Pointer` again on the same token to push the new metadata into the existing pointer contract's bytecode, silently altering `decimals()`/`name()`/`symbol()` for a contract address that other protocols already hold and have cached values for.

### Impact Explanation
Third-party DeFi contracts, wrappers, or vaults that read `decimals()`/`name()`/`symbol()` once at initialization time (a standard and expected assumption for ERC20 tokens, since the standard does not anticipate mutability) and cache the value to compute scaling factors (analogous to Yearn's `precisionFactor`) will silently desynchronize from the pointer's live values. If `decimals` changes (e.g., 6 → 18 or vice versa), any integrator relying on the old cached decimals for deposit/withdraw/swap accounting will mis-scale amounts by orders of magnitude, leading to concrete fund loss or the ability for an attacker to drain a pool/vault that has stale decimals assumptions, or to permanently corrupt accounting for the integrating contract. This is unauthorized value manipulation of ERC20 metadata reachable purely through public transactions/precompile calls and a tokenfactory admin action, without requiring any privileged sei-chain component to be compromised.

### Likelihood Explanation
Reaching this requires: (1) a tokenfactory denom admin (a legitimate "tokenfactory denom creator" per the trust model) calling `MsgSetDenomMetadata` to alter decimals/name/symbol of a denom that already has a registered ERC20 pointer, and (2) any unprivileged EVM caller invoking `addNativePointer` (or `addCW20Pointer`) again for that same denom/address — both of which are ordinary, permissionless operations with no additional guardrails preventing metadata mutation on an existing pointer. There is no warning, versioning bump exposed to callers, or any mechanism forcing downstream integrators to detect the metadata change, making the likelihood of accidental or intentional exploitation realistic wherever pointer contracts are integrated by external protocols that assume ERC20 metadata immutability.

### Recommendation
- Disallow updating `name`/`symbol`/`decimals` for a pointer that already exists and has non-zero activity/integration, or at minimum block metadata changes to `decimals` specifically (the most consequential field for accounting) once a pointer has been created — require deploying a distinct pointer address (new address/version) instead of overwriting bytecode at the same address.
- Emit a strong, indexable event (already partially done via `EventTypePointerRegistered`) specifically flagging metadata mutation distinct from first-time creation, so integrators can react to `decimals()` changes.
- Document explicitly, in the pointer precompile ABI/spec, that pointer ERC20 metadata is not guaranteed immutable, and require third-party integrators to defensively check for metadata drift rather than caching it once.

### Proof of Concept
1. Tokenfactory denom admin creates a denom `factory/{admin}/token` and sets `DenomMetadata` with `decimals = 6` via `MsgSetDenomMetadata` (bank keeper `SetDenomMetaData`) [7](#0-6) .
2. Any user calls the `pointer` precompile's `addNativePointer("factory/{admin}/token")`; a new `NativeSeiTokensERC20` pointer contract is deployed at address `P` with `decimals() == 6` [4](#0-3) .
3. A third-party DeFi contract integrates with `P`, calling `P.decimals()` once and caching `6` to compute a scaling factor for deposits.
4. The denom admin calls `MsgSetDenomMetadata` again, changing the exponent/decimals to `18`.
5. Any unprivileged user re-invokes `addNativePointer("factory/{admin}/token")`; `UpsertERCPointer` detects the pointer already exists and overwrites the bytecode at address `P` via `SetCode`, so `P.decimals()` now returns `18` [8](#0-7) .
6. The third-party contract, still using its cached `decimals = 6` for scaling, now mis-computes token amounts against the pointer's actual 18-decimals behavior by a factor of 10^12, corrupting its internal accounting and enabling fund extraction/loss depending on the integrator's logic — mirroring the exact "precision factor becomes invalid" failure mode described in the yToken report.

### Citations

**File:** precompiles/pointer/pointer.go (L72-93)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *ethabi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if readOnly {
		return nil, 0, errors.New("cannot call pointer precompile from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall pointer")
	}

	switch method.Name {
	case AddNativePointer:
		return p.AddNative(ctx, method, caller, args, value, evm, hooks)
	case AddCW20Pointer:
		return p.AddCW20(ctx, method, caller, args, value, evm, hooks)
	case AddCW721Pointer:
		return p.AddCW721(ctx, method, caller, args, value, evm, hooks)
	case AddCW1155Pointer:
		return p.AddCW1155(ctx, method, caller, args, value, evm, hooks)
	default:
		err = fmt.Errorf("unknown method %s", method.Name)
	}
	return
}
```

**File:** precompiles/pointer/pointer.go (L99-127)
```go
func (p PrecompileExecutor) AddNative(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	token := args[0].(string)
	metadata, metadataExists := p.bankKeeper.GetDenomMetaData(ctx, token)
	if !metadataExists {
		return nil, 0, fmt.Errorf("denom %s does not have metadata stored and thus can only have its pointer set through gov proposal", token)
	}
	name := metadata.Name
	symbol := metadata.Symbol
	var decimals uint8
	for _, denomUnit := range metadata.DenomUnits {
		if denomUnit.Exponent > uint32(decimals) && denomUnit.Exponent <= math.MaxUint8 {
			decimals = uint8(denomUnit.Exponent)
			name = denomUnit.Denom
			symbol = denomUnit.Denom
			if len(denomUnit.Aliases) > 0 {
				name = denomUnit.Aliases[0]
			}
		}
	}
	contractAddr, err := p.evmKeeper.UpsertERCNativePointer(ctx, evm, token, utils.ERCMetadata{Name: name, Symbol: symbol, Decimals: decimals})
	if err != nil {
		return nil, 0, err
	}
```

**File:** x/evm/keeper/pointer_upgrade.go (L89-134)
```go
func (k *Keeper) UpsertERCPointer(
	ctx sdk.Context, evm *vm.EVM, typ string, args []interface{}, getter PointerGetter, setter PointerSetter,
) (contractAddr common.Address, err error) {
	pointee := args[0].(string)
	evmModuleAddress := k.GetEVMAddressOrDefault(ctx, k.AccountKeeper().GetModuleAddress(types.ModuleName))

	var bin []byte
	bin, err = artifacts.GetParsedABI(typ).Pack("", args...)
	if err != nil {
		panic(err)
	}
	bin = append(artifacts.GetBin(typ), bin...)
	// GetDeploymentCode / Create take EVM snapshots that Freeze() Multistore layers.
	// Exists-lookup and commits must use the live unfrozen top (sdb.Ctx): cachekv
	// forbids writing a frozen layer, and same-tx readers that skip frozen-empty
	// parents would miss those writes. The precompile Prepare `ctx` is that top at
	// Prepare time, but is frozen once this Upsert snapshots. Always attach the
	// caller's gas meter (finite precompile meter in deliver) — sdb.Ctx() alone
	// carries the infinite EVM meter.
	sdb := state.GetDBImpl(evm.StateDB)
	liveCtx := func() sdk.Context {
		if sdb == nil {
			return ctx
		}
		return sdb.Ctx().WithGasMeter(ctx.GasMeter())
	}
	existingAddr, _, exists := getter(liveCtx(), pointee)
	suppliedGas := k.getEvmGasLimitFromCtx(ctx)
	var remainingGas uint64
	if exists {
		var ret []byte
		contractAddr = existingAddr
		ret, remainingGas, err = evm.GetDeploymentCode(evmModuleAddress, bin, suppliedGas, utils.Big0, existingAddr)
		if err != nil {
			return
		}
		// Only write on success: a failed GetDeploymentCode can leave ret as nil or
		// revert data, which must not clobber live pointer bytecode (even transiently).
		writeCtx := liveCtx()
		k.SetCode(writeCtx, contractAddr, ret)
		if sdb != nil {
			sdb.RefreshCodeCache(contractAddr, ret)
		}
	} else {
		_, contractAddr, remainingGas, err = evm.Create(evmModuleAddress, bin, suppliedGas, uint256.NewInt(0))
	}
```

**File:** contracts/src/NativeSeiTokensERC20.sol (L7-39)
```text
contract NativeSeiTokensERC20 is ERC20 {

    address constant BANK_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001001;

    string public denom;
    string public nname;
    string public ssymbol;
    uint8 public ddecimals;
    IBank public BankPrecompile;

    constructor(string memory denom_, string memory name_, string memory symbol_, uint8 decimals_) ERC20("", "") {
        BankPrecompile = IBank(BANK_PRECOMPILE_ADDRESS);
        denom = denom_;
        nname = name_;
        ssymbol = symbol_;
        ddecimals = decimals_;
    }

    function name() public view override returns (string memory) {
        return nname;
    }

    function symbol() public view override returns (string memory) {
        return ssymbol;
    }

    function balanceOf(address account) public view override returns (uint256) {
        return BankPrecompile.balance(account, denom);
    }

    function decimals() public view override returns (uint8) {
        return ddecimals;
    }
```

**File:** x/tokenfactory/keeper/msg_server.go (L188-217)
```go
func (server msgServer) SetDenomMetadata(goCtx context.Context, msg *types.MsgSetDenomMetadata) (*types.MsgSetDenomMetadataResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Defense in depth validation of metadata
	err := msg.Metadata.Validate()
	if err != nil {
		return nil, err
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Metadata.Base)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	server.bankKeeper.SetDenomMetaData(ctx, msg.Metadata)

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.TypeMsgSetDenomMetadata,
			sdk.NewAttribute(types.AttributeDenom, msg.Metadata.Base),
			sdk.NewAttribute(types.AttributeDenomMetadata, msg.Metadata.String()),
		),
	})

	return &types.MsgSetDenomMetadataResponse{}, nil
}
```
