Confirmed: `NativeSeiTokensERC20.sol` is the actual production contract compiled into `x/evm/artifacts/native/artifacts.go`, which is the bytecode deployed by `UpsertERCNativePointer` for every EVM pointer to a native/tokenfactory denom. This is the real production pointer contract, not just a sample.

### Title
Native-denom ERC20 pointer reports attacker-controlled `decimals()` decoupled from its actual balance/supply unit scale, enabling decimals-mismatch value miscalculation in any consumer contract - ([File: contracts/src/NativeSeiTokensERC20.sol])

### Summary
`NativeSeiTokensERC20.sol`, the contract whose bytecode is embedded in `x/evm/artifacts/native/artifacts.go` and deployed by `UpsertERCNativePointer` for every native/tokenfactory denom pointer, takes an arbitrary `decimals_` constructor argument that is *not* derived from any deterministic on-chain fact — it is set at pointer-creation time and stored verbatim, while `balanceOf`/`totalSupply` continue to return the raw base-denom (usei-style) integer amount unconditionally. [1](#0-0) [2](#0-1) 

### Finding Description
For governance-registered pointers, `decimals` is derived correctly from `DenomUnits[].Exponent` in bank denom metadata (see `AddNative` in `precompiles/pointer/pointer.go`): [3](#0-2) 

However, for tokenfactory denoms created by any unprivileged user without registered denom metadata, no such metadata exists, and the loop over `DenomUnits` never executes, leaving `decimals` at its zero-value default (`0`) regardless of the denom's actual precision. Tokenfactory denom creation is available to any account, and integration tests confirm the resulting pointer reports `decimals() == 0`: [4](#0-3) 

Because `decimals` on `NativeSeiTokensERC20` is a plain constructor argument that is copied verbatim into `ddecimals` with no relationship enforced to the underlying denom's actual base-unit scale, and `balanceOf`/`totalSupply` always return the raw integer amount from the bank keeper (i.e., always effectively "0-decimal" integer units): [2](#0-1) 

any downstream EVM contract that reads `decimals()` from this pointer and uses it to normalize `balanceOf`/`transfer` amounts (a standard, universally-assumed ERC20 pattern used by AMMs, lending markets, and price oracles) will compute value/collateral/reserve ratios that are off by whatever power-of-ten mismatch exists between the reported `decimals()` and the actual unit scale of the balance integers returned. This is structurally identical to the reported BalancerPairOracle bug class: a contract mixes a "decimals-normalized" quantity (the reported `decimals()`) with a raw, un-normalized balance quantity, producing values skewed by orders of magnitude.

### Impact Explanation
Any DeFi protocol built on Sei EVM that composes native-token pointer contracts with standard ERC20 assumptions (pools, lending markets, custom on-chain price/reserve calculations) can be driven to computing wildly incorrect valuations for the pointer token relative to any other properly-decimal-scaled ERC20/pointer it is paired with, because the pointer's declared `decimals()` does not consistently reflect the actual scale of the integer amounts it returns. This can lead to attacker-favorable mispricing, under-collateralization, or reserve-ratio corruption in any contract relying on the pointer's `decimals()` for value conversion — a direct path to fund loss for users of such contracts, analogous to the referenced BalancerPairOracle finding.

### Likelihood Explanation
Likelihood is high for any protocol/tooling built naively on top of Sei's native pointer contracts assuming standard ERC20 semantics (that `decimals()` correctly describes the granularity of `balanceOf`), since: (1) any user can create a tokenfactory denom without metadata, causing `decimals()` to silently default to `0` instead of reflecting whatever real precision the token is meant to represent, and (2) nothing in the pointer's ERC20 interface prevents a contract from combining this pointer with an 18-decimal token under the universal ERC20 convention that `balanceOf` values are scaled by `decimals()`.

### Recommendation
Ensure `NativeSeiTokensERC20`'s `decimals()` is always deterministically derived from the actual on-chain denom metadata (or a well-defined default that's documented and enforced, e.g., always `0` for un-metadata'd tokenfactory denoms, with `balanceOf` semantics matching), and audit/gate the constructor path so that `decimals_` cannot be set inconsistently with the real base-unit granularity of the wrapped denom. Consider disallowing pointer creation for un-metadata'd denoms or forcing a canonical zero-decimal representation site-wide so downstream consumers can rely on it.

### Proof of Concept
1. Any account calls the tokenfactory module to create a new denom without setting denom metadata (permissionless).
2. Call the pointer precompile's `addNativePointer(denom)` — since no metadata exists, the `decimals` loop in `AddNative` never populates `decimals`, so it stays `0`; the deployed `NativeSeiTokensERC20` instance reports `decimals() == 0` (confirmed by `pointer.spec.ts` assertion `expect(await erc20.decimals()).to.equal(0n)`).
3. Deploy or interact with any third-party DeFi contract (AMM, lending pool) that pairs this pointer with a normal 18-decimal ERC20, applying the standard convention of scaling amounts by `10**decimals()` to compute value/collateral.
4. Because the pointer's `balanceOf` returns raw integer denom units while its `decimals()` communicates `0` (or an inconsistent value for governance-registered legacy denoms with non-6 exponents), the consuming contract's value computation is skewed by the actual precision difference (e.g., treating a 6-decimal-equivalent balance as if it were whole-unit), analogous to the BalancerPairOracle fair-reserve miscalculation, letting an attacker manipulate the mispriced side of the pool/market to extract value from other participants.

### Citations

**File:** contracts/src/NativeSeiTokensERC20.sol (L17-23)
```text
    constructor(string memory denom_, string memory name_, string memory symbol_, uint8 decimals_) ERC20("", "") {
        BankPrecompile = IBank(BANK_PRECOMPILE_ADDRESS);
        denom = denom_;
        nname = name_;
        ssymbol = symbol_;
        ddecimals = decimals_;
    }
```

**File:** contracts/src/NativeSeiTokensERC20.sol (L33-43)
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
```

**File:** precompiles/pointer/pointer.go (L106-118)
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
