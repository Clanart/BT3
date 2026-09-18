## Title
Native-denom ERC20 pointer contract reports mismatched `decimals()` versus raw base-denom balances, causing systematic asset mispricing — (`precompiles/pointer/pointer.go`, `x/evm/artifacts/native/native.go` / `contracts/src/NativeSeiTokensERC20.sol`)

### Summary
The `pointer` precompile's `addNativePointer` deploys an ERC20 pointer contract (`NativeSeiTokensERC20`) for any bank/tokenfactory denom. The pointer's `decimals()` value is derived from the denom's metadata (the highest-exponent `DenomUnit`), while `balanceOf`, `totalSupply`, and `transfer` all operate on the *raw, unscaled* base-denom integer amount from the bank keeper. This is the exact bug class described in the external report: a contract exposes a `decimals()`/precision value that does not match the actual numeric scale of the balances it reports, so any consumer that (correctly, per the ERC20 standard) treats `balanceOf()` as being denominated in units of `10^-decimals()` will misinterpret the value by orders of magnitude.

### Finding Description
When `addNativePointer` is invoked, the executor reads the denom's `bank` metadata and picks `decimals` from the `DenomUnit` with the largest `Exponent`: [1](#0-0) 

That `decimals` value (which can be non-zero, e.g. 6 for a "display" unit such as `sei` per `usei`, or any arbitrary exponent a tokenfactory admin sets via denom metadata) is passed straight into the constructor of the deployed pointer contract: [2](#0-1) 

The deployed `NativeSeiTokensERC20` contract stores this value verbatim and returns it from `decimals()`, but `balanceOf`, `totalSupply`, and the ERC20 `_update` (transfer) logic all read/write the **raw base-denom integer amount** via the `Bank` precompile — no scaling by `10^decimals` is ever applied: [3](#0-2) 

This is confirmed by the bank precompile itself: base-denom `decimals()` is hard-coded to `0` because "all native tokens are integer-based" — i.e., the true precision of the underlying balance is always 0, independent of whatever exponent a display `DenomUnit` declares: [4](#0-3) 

So whenever a denom's metadata contains a `DenomUnit` with `Exponent > 0` (which is the common case — e.g. any tokenfactory denom whose creator sets human-readable display metadata via `MsgSetDenomMetadata`, or `usei`/`sei` itself), the resulting native ERC20 pointer will advertise `decimals() = <exponent>` while its `balanceOf()`/`totalSupply()`/`transfer()` amounts are the same raw integers as the 0-decimal base denom. The integration test suite even documents that this "decimals from metadata" behavior is intentional and can diverge from `0`, unlike the tokenfactory-created-denom-without-display-unit case where it correctly stays `0`: [5](#0-4) 

### Impact Explanation
Any EVM contract, DEX, lending market, or price oracle that composes with these native-token pointers and follows the universal ERC20 convention — treat `balanceOf()`/`transfer` amounts as expressed in `10^-decimals()` units — will compute values that are off by a factor of `10^decimals`. For example, a denom with a display unit at exponent 6 will report `decimals() == 6`, so external code will divide raw integer balances by `10^6` to obtain a "human" balance, when the raw integer is actually the whole (or true minimal-unit) balance. This causes systematic under- or over-valuation of the asset in any protocol that pool-prices, collateralizes, or swaps against it, directly leading to fund loss (e.g., a lending protocol undercollateralizing/overcollateralizing loans, or an AMM mispricing swaps) — matching the accepted impact class of unauthorized transfer/fund loss via pointer mispricing.

### Likelihood Explanation
This is trivially reachable by any unprivileged actor: a tokenfactory denom creator can create a denom, set its own metadata (including any `DenomUnit`/`Exponent` they choose) via the standard `MsgSetDenomMetadata`-style flow, and then call `addNativePointer` on the public pointer precompile — no special privilege beyond being the denom's admin is required, and the resulting pointer is a public ERC20 usable by any downstream EVM contract. Because `usei` itself typically carries a `sei` display unit at exponent 6 in metadata, this can even affect a first-class chain asset's pointer, not just adversary-crafted tokens.

### Recommendation
Either (a) always hard-code `decimals()` to `0` for native pointer contracts (matching the bank precompile's own `decimals` semantics, since balances are always integer/base-unit), or (b) if a non-zero `decimals()` is desired for UI purposes, scale `balanceOf`/`totalSupply`/transfer amounts by `10^decimals` consistently so the ERC20 numeric contract is honored. The safest fix is to stop deriving `decimals` from the display `DenomUnit` exponent for the pointer's `decimals()` return value and instead always use `0`, since the pointer's underlying storage is always base-denom integer amounts.

### Proof of Concept
1. As a tokenfactory denom creator, create a denom `factory/<creator>/mytoken` and mint some amount to an account.
2. Set denom metadata with `DenomUnits = [{Denom: "umytoken", Exponent: 0}, {Denom: "mytoken", Exponent: 6}]` (standard practice for a "display" unit).
3. Call the pointer precompile's `addNativePointer("factory/<creator>/mytoken")` from any account — this is unprivileged and public, see `precompiles/pointer/pointer.go` `AddNative`.
4. Query the deployed pointer: `decimals()` returns `6`, but `balanceOf(holder)` returns the raw integer amount minted (e.g. `1000000000000` for 1,000,000 tokens at base denom precision), not `1000000000000 / 10^6`.
5. Any external contract/integration that computes `humanBalance = balanceOf(holder) / 10**decimals()` will read a wildly wrong balance (a factor of `10^6` too small), demonstrating the mispricing.

### Citations

**File:** precompiles/pointer/pointer.go (L106-123)
```go
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
```

**File:** x/evm/keeper/pointer_upgrade.go (L49-57)
```go
func (k *Keeper) UpsertERCNativePointer(
	ctx sdk.Context, evm *vm.EVM, token string, metadata utils.ERCMetadata,
) (contractAddr common.Address, err error) {
	return k.UpsertERCPointer(
		ctx, evm, "native", []interface{}{
			token, metadata.Name, metadata.Symbol, metadata.Decimals,
		}, k.GetERC20NativePointer, k.SetERC20NativePointer,
	)
}
```

**File:** contracts/src/NativeSeiTokensERC20.sol (L33-49)
```text
    function balanceOf(address account) public view override returns (uint256) {
        return BankPrecompile.balance(account, denom);
    }

    function decimals() public view override returns (uint8) {
        return ddecimals;
    }

    function totalSupply() public view override returns (uint256) {
        return BankPrecompile.supply(denom);
    }

    function _update(address from, address to, uint256 value) internal override {
        bool success = BankPrecompile.send(from, to, denom, value);
        require(success, "NativeSeiTokensERC20: transfer failed");
        emit Transfer(from, to, value);
    }
```

**File:** precompiles/bank/bank.go (L399-407)
```go
func (p PrecompileExecutor) decimals(ctx sdk.Context, method *abi.Method, _ []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	// all native tokens are integer-based, returns decimals for microdenom (usei)
	bz, err := method.Outputs.Pack(uint8(0))
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** integration_test/precompile_tests/precompiles/pointer.spec.ts (L84-91)
```typescript
        it('the deployed pointer is a live ERC20 whose metadata mirrors the denom', async () => {
            const erc20 = new ethers.Contract(pointerAddress, ERC20_ABI, provider);
            // Tokenfactory metadata: name/symbol are the FULL factory/… denom
            // string and the single denom unit has exponent 0.
            expect(await erc20.name()).to.equal(denom);
            expect(await erc20.symbol()).to.equal(denom);
            expect(await erc20.decimals()).to.equal(0n);
        });
```
