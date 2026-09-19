Found a strong analog: the `Claim` function in the `solo` precompile transfers **all** balances of an unassociated legacy account to whichever EVM caller claims it, without any token-level validation or whitelist. Because a legacy Sei account's address is public/derivable (it's the bech32 form of a known pubkey/EVM caller), anyone can permissionlessly `bank send` arbitrary/malicious token denoms (including malicious CW20-pointer-backed or tokenfactory tokens with deceptive metadata) to that `sender` address before the real owner claims it. When the legitimate owner calls `claim()`, `GetAllBalances` sweeps every denom in that account — legitimate and attacker-planted alike — straight to the claimer's associated address, exactly mirroring the OpenQ pattern of an unvetted, attacker-added asset being automatically included in a distribution to a victim.

### Title
Solo precompile `Claim` sweeps and delivers unvetted attacker-planted tokens to claimer - (File: `precompiles/solo/solo.go`)

### Summary
The `solo` precompile's `Claim` method (`ClaimMethod`) transfers **all** bank balances held by a legacy (pre-EVM-association) Sei account to the caller's Sei address in one unconditional `SendCoins(ctx, sender, ..., p.bankKeeper.GetAllBalances(ctx, sender))` call, with no filtering of which denoms are legitimate.

### Finding Description
`Claim` resolves the legacy account (`sender`) from a signed claim message and, after signature verification in `validate`/`sigverify`, executes: [1](#0-0) 
This sweeps every coin denom present in `sender`'s balance — there is no allow-list, no restriction to a known/expected denom, and no interaction from `sender`'s legitimate owner required to receive funds into that account. Since `sender` is a plain bech32 `sdk.AccAddress` (derivable from any known pubkey/address that has not yet migrated to EVM), any unprivileged third party can permissionlessly `MsgSend` an arbitrary or malicious token (e.g., a maliciously-named tokenfactory denom, or a token registered through the ERC20/CW20 pointer precompile at [2](#0-1)  with attacker-controlled name/symbol metadata) into that address before the real owner calls `claim`. This exactly parallels the OpenQ pattern: an unrelated party funds a token into a balance/collection that is later blindly iterated/swept and handed to a claimer without any funder-identity or token-safety check.

### Impact Explanation
When the true owner of the legacy account later calls `claim()` from their newly associated EVM address, `GetAllBalances` unconditionally includes the attacker-planted malicious token alongside legitimate funds, and it is transferred to the claimer. The claimer, trusting that the funds came from the Sei precompile/claim flow, may then interact with the malicious token contract (e.g., approve/transfer it), exposing them to phishing, fake-approval drains, or other malicious-token interactions. This does not directly cause loss from the chain's accounting standpoint, but it does silently deliver unvetted third-party assets to a user via a core system precompile, which can be leveraged to defraud users interacting with the resulting balance.

### Likelihood Explanation
Likelihood is high for the setup step: sending an arbitrary coin to any known bech32 address is a normal, permissionless `bank.Send`/`MsgSend` operation reachable by any transaction sender, requiring no special privilege, and the target address (the legacy account awaiting claim) is often publicly known ahead of an EVM association/claim event.

### Recommendation
`Claim` should only transfer coins that were present/expected at the time of association (e.g., a snapshotted balance or an explicit allow-list of denoms), rather than unconditionally sweeping `GetAllBalances` at claim time. Alternatively, filter out denoms not present in a known safe-list or added after a cutoff block, similar to `ClaimSpecific`'s explicit per-asset approach, and surface unexpected new denoms to the user rather than auto-transferring them.

### Proof of Concept
1. Identify a legacy (pre-association) Sei account address `A` that has not yet called `solo.claim()`.
2. Attacker sends an arbitrary/malicious coin (e.g., a tokenfactory denom crafted with deceptive `Metadata`, or the ERC20/CW20 pointer of a scam token) to `A` via a normal `MsgSend`.
3. The legitimate owner of `A` later calls the `solo` precompile's `claim(txBytes)` method (`ClaimMethod`) from their EVM address, per the flow in [3](#0-2) .
4. `p.bankKeeper.GetAllBalances(ctx, sender)` returns both the legitimate coins and the attacker's planted malicious coin; `SendCoins` transfers all of them to the claimer's Sei address in one transaction, with no warning or filtering.

### Citations

**File:** precompiles/solo/solo.go (L140-155)
```go
func (p PrecompileExecutor) Claim(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, readOnly bool) (ret []byte, remainingGas uint64, err error) {
	claimMsg, sender, err := p.validate(ctx, caller, args, readOnly)
	if err != nil {
		return nil, 0, err
	}
	_, ok := claimMsg.(claimSpecificMsg)
	if ok {
		return nil, 0, errors.New("message for Claim must not be MsgClaimSpecific type")
	}
	if err := p.bankKeeper.SendCoins(ctx, sender,
		p.evmKeeper.GetSeiAddressOrDefault(ctx, caller), p.bankKeeper.GetAllBalances(ctx, sender)); err != nil {
		return nil, 0, fmt.Errorf("failed to transfer coins: %w", err)
	}
	bz, err := method.Outputs.Pack(true)
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** precompiles/pointer/legacy/v562/pointer.go (L115-171)
```go
func (p PrecompileExecutor) AddNative(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	token := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20NativePointer(ctx, token)
	if exists && existingVersion >= 1 {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, native.CurrentVersion)
	}
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
	constructorArguments := []interface{}{
		token, name, symbol, decimals,
	}

	packedArgs, err := native.GetParsedABI().Pack("", constructorArguments...)
	if err != nil {
		panic(err)
	}
	bin := append(native.GetBin(), packedArgs...)
	if value == nil {
		value = utils.Big0
	}
	ret, contractAddr, remainingGas, err := evm.Create(caller, bin, suppliedGas, uint256.MustFromBig(value))
	if err != nil {
		return
	}
	err = p.evmKeeper.SetERC20NativePointer(ctx, token, contractAddr)
	if err != nil {
		return
	}

	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "native"),
		sdk.NewAttribute(types.AttributeKeyPointerAddress, contractAddr.Hex()), sdk.NewAttribute(types.AttributeKeyPointee, token),
		sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", native.CurrentVersion))))
	ret, err = method.Outputs.Pack(contractAddr)
	return
}
```
