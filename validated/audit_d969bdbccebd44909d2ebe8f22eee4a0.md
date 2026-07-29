### Title
Permissionless registration of a malicious ERC20 contract allows unbacked minting of native Cosmos coins via `ConvertERC20` self-reported balances - (File: `x/erc20/keeper/msg_server.go`, `x/erc20/keeper/proposals.go`)

### Summary
The external Pendle report's root cause is trusting a *self-reported conversion rate* (SY→Yield Token assumed 1:1) from a component the protocol does not fully control, instead of verifying the real backing value. The Cosmos EVM `x/erc20` module has a structurally analogous flaw: when converting a "native ERC20" token pair into native Cosmos coins via `ConvertERC20`, the code assumes that a `balanceOf` increase reported by the *same, arbitrary, permissionlessly-registered* ERC20 contract accurately reflects tokens actually escrowed, and mints native coins 1:1 against that self-reported value.

### Finding Description
`RegisterERC20` allows permissionless registration of any ERC20 contract address as a `TokenPair` with `OWNER_EXTERNAL` when `params.PermissionlessRegistration` is enabled [1](#0-0) , delegating to `registerERC20`, which only validates that the contract responds to `name/symbol/decimals` — it performs no validation of the contract's actual token-accounting logic [2](#0-1) .

When a user calls `ConvertERC20` for such a "native ERC20" pair, `convertERC20IntoCoinsForNativeToken`:
1. Reads `balanceToken` via `k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)` — a `staticcall` to `balanceOf` on the **attacker-controlled contract** [3](#0-2) .
2. Calls `transfer(ModuleAddress, amount)` on that same contract [4](#0-3) .
3. Re-reads `balanceTokenAfter` via `balanceOf` on the **same attacker-controlled contract**, and only checks that it increased by exactly `amount` [5](#0-4) .
4. If this self-reported check passes, the module **mints real native Cosmos coins** and sends them to the receiver [6](#0-5) .

The entire invariant that "1 escrowed ERC20 token backs 1 minted native coin" is enforced only by calling `balanceOf` twice on the *same untrusted contract*. Because the attacker fully controls that contract's bytecode, `balanceOf` can trivially be made to report `balanceToken + amount` on the second call regardless of whether any tokens were actually escrowed (e.g., `transfer` can be a no-op, and `balanceOf` can simply return a value based on call count, block height, or ignore state entirely). This is exactly the "SY == Yield Token" fallacy from the Pendle report: the protocol assumes a self-reported quantity from an external, unverified contract accurately represents a real, immutable 1:1-backed value, and mints spendable native-chain value against that assumption without any independent verification (e.g., verifying actual on-chain token supply changes, requiring a whitelisted/audited implementation, or cross-checking via an independent oracle/verifier).

### Impact Explanation
This allows unauthorized, unbacked minting of native Cosmos coins from thin air: an attacker registers a malicious ERC20 contract as a token pair, then repeatedly calls `ConvertERC20` to mint arbitrary amounts of the paired native denom without ever having escrowed real value. This directly matches the Critical impact gate: "unauthorized minting... of spendable user value across native balances." It inflates the native coin supply, corrupting bank-module accounting invariants and enabling the attacker to drain real liquidity/collateral wherever that native denom is used (DEXs, lending, IBC transfers out to other chains, ERC20 precompile wrapping, etc.), directly threatening protocol solvency exactly as described for the original Pendle finding.

### Likelihood Explanation
The trigger is fully permissionless if `params.PermissionlessRegistration` is enabled (a supported, non-privileged configuration querying `k.GetParams(ctx).PermissionlessRegistration`) [1](#0-0) . Even if permissionless registration is disabled by default, this is a standard governance-approved configuration parameter for many chains adopting Cosmos EVM (to allow open onboarding of ERC20 tokens), and any chain that enables it is immediately exposed. No relayer, validator, or privileged account is required — a single unprivileged EOA deploying one malicious contract and calling two standard module messages (`RegisterERC20`, `ConvertERC20`) is sufficient.

### Recommendation
- Do not rely solely on the registered contract's own `balanceOf` return values to validate escrow. Cross-check against expected supply/allowance invariants that cannot be manipulated by the contract itself (e.g., require the ERC20 to be a known/audited minter-burner implementation, or enforce escrow verification through an independent mechanism such as tracking transfer `Transfer` event logs from a canonical deployed bytecode hash, and reject contracts with unverified/arbitrary bytecode).
- Restrict `OWNER_EXTERNAL` (native ERC20) pairs backing mintable native coins to only well-known, verified/whitelisted implementations, or require a bytecode-hash allowlist (e.g., only accept the canonical `ERC20MinterBurnerDecimalsContract` bytecode the module itself deploys) for pairs eligible for coin minting via `ConvertERC20`.
- Alternatively, gate `PermissionlessRegistration` combined with mint-eligible conversion behind additional collateralization checks that do not depend on the registered contract's self-reported state.

### Proof of Concept
1. Chain governance (or default config) has `params.PermissionlessRegistration = true`.
2. Attacker deploys a malicious ERC20 contract `Evil` where:
   - `transfer(to, amount)` always returns `true` and does not move any real balance.
   - `balanceOf(account)` returns a value that increases by `amount` on demand (e.g., tracked via an internal counter unrelated to actual token movement, or simply always reports "whatever is needed" via `tx.origin`/`msg.sender` tricks since the module calls it via `staticcall` with `ModuleAddress` as `from`).
3. Attacker calls `MsgRegisterERC20{Erc20Addresses: [Evil]}` — succeeds permissionlessly, creating a `TokenPair` with `OWNER_EXTERNAL` and a real Cosmos denom.
4. Attacker calls `MsgConvertERC20{ContractAddress: Evil, Amount: 1_000_000_000, Receiver: attacker}`.
5. `convertERC20IntoCoinsForNativeToken` reads `balanceToken` from `Evil.balanceOf(ModuleAddress)`, calls `Evil.transfer(ModuleAddress, amount)` (no real effect), reads `balanceTokenAfter` from `Evil.balanceOf(ModuleAddress)` which the attacker's contract reports as `balanceToken + amount` regardless of actual state.
6. The invariance check at msg_server.go:124-130 passes since the contract lied consistently.
7. `k.bankKeeper.MintCoins` mints `1_000_000_000` units of the new denom, and `SendCoinsFromModuleToAccount` sends them to the attacker — real, spendable native coins created from nothing.

### Citations

**File:** x/erc20/keeper/msg_server.go (L86-95)
```go
	// Escrow tokens on module account
	transferData, err := erc20.Pack("transfer", types.ModuleAddress, msg.Amount.BigInt())
	if err != nil {
		return nil, err
	}

	res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
	if err != nil {
		return nil, err
	}
```

**File:** x/erc20/keeper/msg_server.go (L113-130)
```go
	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L132-140)
```go
	// Mint coins
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}

	// Send minted coins to the receiver
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins); err != nil {
		return nil, err
	}
```

**File:** x/erc20/keeper/msg_server.go (L324-341)
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

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

```

**File:** x/erc20/keeper/proposals.go (L18-42)
```go
func (k Keeper) registerERC20(
	ctx sdk.Context,
	contract common.Address,
) (*types.TokenPair, error) {
	// Check if ERC20 is already registered
	if k.IsERC20Registered(ctx, contract) {
		return nil, errorsmod.Wrapf(
			types.ErrTokenPairAlreadyExists, "token ERC20 contract already registered: %s", contract.String(),
		)
	}

	metadata, err := k.CreateCoinMetadata(ctx, contract)
	if err != nil {
		return nil, errorsmod.Wrap(
			err, "failed to create wrapped coin denom metadata for ERC20",
		)
	}

	pair := types.NewTokenPair(contract, metadata.Name, types.OWNER_EXTERNAL)
	err = k.SetToken(ctx, pair)
	if err != nil {
		return nil, err
	}
	return &pair, nil
}
```

**File:** x/erc20/keeper/evm.go (L137-159)
```go
// BalanceOf queries an account's balance for a given ERC20 contract
func (k Keeper) BalanceOf(
	ctx sdk.Context,
	abi abi.ABI,
	contract, account common.Address,
) *big.Int {
	res, err := k.evmKeeper.CallEVM(ctx, abi, types.ModuleAddress, contract, false, nil, "balanceOf", account)
	if err != nil {
		return nil
	}

	unpacked, err := abi.Unpack("balanceOf", res.Ret)
	if err != nil || len(unpacked) == 0 {
		return nil
	}

	balance, ok := unpacked[0].(*big.Int)
	if !ok {
		return nil
	}

	return balance
}
```
