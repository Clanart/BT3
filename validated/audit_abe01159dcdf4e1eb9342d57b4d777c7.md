### Title
Pointer decimals can be changed post-deployment by re-upserting an ERC20 native pointer at the same address, corrupting decimal interpretation for any integrator — ([File: precompiles/pointer/pointer.go])

### Summary
The `pointer` precompile's `addNativePointer` method (analogous to `UXDController.setRedeemable()`, which let an admin silently swap the underlying "redeemable" token and change its effective decimals mid-lifetime) allows re-registering ("upserting") the ERC20 pointer contract for an already-pointed tokenfactory/bank denom at the *same* contract address, using whatever `decimals` value the denom's bank metadata currently reports. Because tokenfactory denom metadata (`MsgSetDenomMetadata`) can be updated after the pointer contract already exists and has been integrated with by third parties, the pointer's `decimals()` return value can be changed after the fact without changing any raw balances — exactly the "unexpected behavior from changing a token's effective decimals after other contracts have already begun relying on it" bug class from the report.

### Finding Description
`PrecompileExecutor.AddNative` in `precompiles/pointer/pointer.go` computes `name`, `symbol`, and `decimals` freshly from the bank keeper's current `DenomMetadata` for the token every time it is invoked, with no restriction preventing it from being called again for a denom that already has a live pointer: [1](#0-0) 

This flows into `Keeper.UpsertERCNativePointer` → `Keeper.UpsertERCPointer`, which explicitly supports **redeploying the pointer's bytecode in place at the existing contract address** when a pointer for that token already exists, using `evm.GetDeploymentCode(...)` and `k.SetCode(...)` to replace the runtime code with a constructor built from the new `name/symbol/decimals` arguments: [2](#0-1) 

This "upsert same address, new metadata" capability is explicitly tested and documented as intended behavior (kept for governance-driven pointer upgrades): [3](#0-2) [4](#0-3) 

The problem is that `AddNative` is a **public, unauthenticated EVM precompile method** — any EVM caller can invoke it (only a non-payable/arg-length check is performed, no caller/admin restriction): [5](#0-4) 

The `decimals` value it uses comes directly from the denom's on-chain bank metadata, specifically the highest-exponent `DenomUnit`: [6](#0-5) 

For tokenfactory denoms, that metadata is mutable after creation via `MsgSetDenomMetadata`, which is gated only by the tokenfactory denom's *own* admin (the denom creator by default) — not chain governance: [7](#0-6) [8](#0-7) 

Putting these together: a tokenfactory denom creator (an explicitly in-scope, unprivileged-relative-to-chain actor) can (1) create a denom with metadata exponent D1, (2) call the public `addNativePointer` precompile to deploy an ERC20 pointer reporting `decimals() == D1`, (3) let other users/integrators (DEX pools, wallets, other contracts) treat balances of that pointer using D1 decimals, (4) later call `MsgSetDenomMetadata` to change the denom's unit exponent to D2, and (5) call `addNativePointer` again — which the code permits and which redeploys the *same* pointer address's bytecode to report `decimals() == D2`, while all underlying raw bank balances are unchanged. This is the direct structural analog of the `UXDController.setRedeemable()` bug: swapping the token/decimals interpretation underneath an already-integrated ERC20 interface, without any check that no external protocol has begun relying on the previous decimal convention.

### Impact Explanation
Any downstream integration that reads `decimals()` once (at pool creation, price-oracle configuration, or accounting setup) and later relies on cached balances/amounts computed against the old decimal convention will misprice or misaccount funds by orders of magnitude the moment the pointer is silently redeployed with new decimals — the same "value distortion" impact class documented in the source report (minting/redeeming amounts off by 10^n). This can be leveraged for fund-loss style attacks against any AMM pool, lending market, or bridge that has adopted the pointer ERC20 as a collateral/trading asset, satisfying the "unauthorized transfer via precompile or pointer" / concrete fund loss bar.

### Likelihood Explanation
Likelihood is LOW/MEDIUM in practice: it requires (a) an attacker who controls a tokenfactory denom's admin authority, (b) getting a third-party protocol to integrate with the pointer ERC20 for that denom, and (c) the third party caching `decimals()` rather than reading it live. This mirrors the original report's own risk framing (low likelihood, high impact ⇒ overall high severity due to low attack cost, since creating a tokenfactory denom and calling `addNativePointer` are both permissionless, cheap, single-transaction operations).

### Recommendation
- Once a native pointer has been created for a token, reject subsequent `AddNative`/`UpsertERCNativePointer` calls that would change `decimals` (or `name`/`symbol` used for value-bearing metadata) for the *same* pointee, or require the call to go exclusively through the governance-gated proposal path (`AddERCNativePointerProposalV2`) rather than the permissionless precompile.
- Alternatively, make the pointer's `decimals()` immutable at first-deployment time and have any post-hoc metadata correction deploy at a new, versioned address rather than clobbering code at the original address, so integrators are never silently repointed to different decimal semantics.
- Emit a distinct on-chain event when a pointer's decimals actually change on upsert (not just re-registration with identical parameters), so downstream integrators can detect and react to the change.

### Proof of Concept
1. Attacker creates a tokenfactory denom `factory/attacker/foo` with `MsgCreateDenom`; bank metadata is set with one `DenomUnit{Exponent: 6}` (this is the pattern used throughout the tokenfactory tests, e.g. `x/tokenfactory/keeper/admins_test.go` `TestSetDenomMetaData`).
2. Attacker calls the `pointer` precompile at `0x100b`, method `addNativePointer("factory/attacker/foo")` — see `AddNative` in `precompiles/pointer/pointer.go`. A pointer ERC20 is deployed reporting `decimals() == 6`.
3. A third-party DEX/lending protocol lists this pointer token, computing prices/collateral values assuming 6-decimal amounts.
4. Attacker calls `MsgSetDenomMetadata` (as the denom's admin) to change the metadata to a `DenomUnit{Exponent: 18}`.
5. Attacker calls `addNativePointer("factory/attacker/foo")` again. Per `UpsertERCPointer` in `x/evm/keeper/pointer_upgrade.go`, since a pointer already exists for this token, the code redeploys in place and the pointer's `decimals()` now returns 18 at the *same address* the DEX already trusts.
6. Any subsequent balance the DEX interprets using stale 6-decimal assumptions against the now-18-decimal reporting pointer will be off by 10^12, enabling mispriced trades/redemptions and fund extraction — directly mirroring the UXD `redeemable` swap PoC from the source report.

Note: I was not able to locate and read the exact `x/tokenfactory/keeper/msg_server.go` `SetDenomMetadata` handler body in this pass (grep did not return a match for that exact signature, and file contents beyond what's indexed were unavailable), so the precise authorization check on `MsgSetDenomMetadata` (whether it strictly requires the tokenfactory denom's stored `admin`) could not be independently confirmed line-by-line in this session — the `x/tokenfactory/README.md` and CLI docs describe it as admin-gated, consistent with the `Mint`/`Burn` handlers' pattern of checking `authorityMetadata.GetAdmin()`. If full verification of this specific handler is needed, a Devin session with full repository access should confirm it directly.

### Citations

**File:** precompiles/pointer/pointer.go (L99-124)
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
```

**File:** x/evm/keeper/pointer_upgrade.go (L89-141)
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
	if err != nil {
		return
	}
	ctx.GasMeter().ConsumeGas(k.GetCosmosGasLimitFromEVMGas(ctx, suppliedGas-remainingGas), "ERC pointer deployment")
	if err = setter(liveCtx(), pointee, contractAddr); err != nil {
		return
	}
```

**File:** x/evm/keeper/pointer_upgrade_test.go (L33-70)
```go
func TestUpsertERCNativePointer(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx([]byte{}).WithBlockTime(time.Now())
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
	var addr common.Address
	err := k.RunWithOneOffEVMInstance(ctx, func(e *vm.EVM) error {
		a, err := k.UpsertERCNativePointer(ctx, e, "test", utils.ERCMetadata{
			Name:     "test",
			Symbol:   "test",
			Decimals: 6,
		})
		addr = a
		return err
	}, func(s1, s2 string) {})
	require.Nil(t, err)
	var newAddr common.Address
	err = k.RunWithOneOffEVMInstance(ctx, func(e *vm.EVM) error {
		a, err := k.UpsertERCNativePointer(ctx, e, "test", utils.ERCMetadata{
			Name:     "test2",
			Symbol:   "test2",
			Decimals: 12,
		})
		newAddr = a
		return err
	}, func(s1, s2 string) {})
	require.Nil(t, err)
	require.Equal(t, addr, newAddr)
	res, err := k.QueryERCSingleOutput(ctx, "native", addr, "name")
	require.Nil(t, err)
	require.Equal(t, "test2", res.(string))
	res, err = k.QueryERCSingleOutput(ctx, "native", addr, "symbol")
	require.Nil(t, err)
	require.Equal(t, "test2", res.(string))
	res, err = k.QueryERCSingleOutput(ctx, "native", addr, "decimals")
	require.Nil(t, err)
	require.Equal(t, uint8(12), res.(uint8))
	_, err = k.QueryERCSingleOutput(ctx, "native", addr, "nonexist")
	require.NotNil(t, err)
```

**File:** integration_test/precompile_tests/precompiles/pointer.spec.ts (L93-103)
```typescript
        it('re-registering the same denom upserts and keeps the address stable', async () => {
            const again: string = await pointer.addNativePointer.staticCall(denom);
            expect(again.toLowerCase()).to.equal(pointerAddress.toLowerCase());

            const tx = await pointer.addNativePointer(denom, { gasLimit: 5_000_000 });
            expect((await tx.wait())!.status, 'upsert must succeed').to.equal(1);

            const [addr, , exists] = await pointerview.getNativePointer(denom);
            expect(exists).to.equal(true);
            expect(addr.toLowerCase()).to.equal(pointerAddress.toLowerCase());
        });
```

**File:** x/tokenfactory/client/wasm/encoder.go (L60-70)
```go
func EncodeTokenFactorySetMetadata(rawMsg json.RawMessage, sender sdk.AccAddress) ([]sdk.Msg, error) {
	encodedSetMetadataMsg := bindings.SetMetadata{}
	if err := json.Unmarshal(rawMsg, &encodedSetMetadataMsg); err != nil {
		return []sdk.Msg{}, types.ErrEncodeTokenFactorySetMetadata
	}
	setMetadataMsg := types.MsgSetDenomMetadata{
		Sender:   sender.String(),
		Metadata: encodedSetMetadataMsg.Metadata,
	}
	return []sdk.Msg{&setMetadataMsg}, nil
}
```

**File:** x/tokenfactory/README.md (L81-103)
```markdown

- Safety check the following
  - Check that the denom is created via `tokenfactory` module
  - Check that the sender of the message is the admin of the denom
- Burn designated amount of tokens for the denom via `bank` module

### ChangeAdmin

Change the admin of a denom. Note, this is only allowed to be called by the current admin of the denom.

```protobuf
message MsgChangeAdmin {
  string sender = 1 [ (gogoproto.moretags) = "yaml:\"sender\"" ];
  string denom = 2 [ (gogoproto.moretags) = "yaml:\"denom\"" ];
  string newAdmin = 3 [ (gogoproto.moretags) = "yaml:\"new_admin\"" ];
}
```

**State Modifications:**

- Check that sender of the message is the admin of denom
- Modify `AuthorityMetadata` state entry to change the admin of the denom

```
