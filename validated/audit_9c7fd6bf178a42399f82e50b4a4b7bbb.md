### Permanent token lock due to inconsistent accounting in self-destructed ERC-20 pairs - (`x/erc20/keeper/msg_server.go`)

### Summary
The `x/erc20` module fails to update the bank module's supply accounting when a native ERC-20 contract is self-destructed. While the module correctly deletes the `TokenPair` from its own state, it does not burn the escrowed Cosmos coins or provide a mechanism to recover the underlying ERC-20 tokens previously locked in the module's escrow. This leads to a permanent lock of funds and an irreversible accounting corruption where the `bank` module's total supply reflects coins that can no longer be converted back to ERC-20.

### Finding Description
In the Cosmos EVM `x/erc20` module, `ConvertERC20` is used to escrow ERC-20 tokens and mint corresponding Cosmos coins. For "Native ERC20" pairs, the module escrows the ERC-20 tokens in the `x/erc20` module address and mints new coins via the `bank` module [1](#0-0) .

If the underlying ERC-20 contract is self-destructed (suicided), the `ConvertERC20` and `ConvertCoin` functions detect this by checking if the account has code [2](#0-1) . When a self-destructed contract is detected, the `Keeper` deletes the `TokenPair` from the state [3](#0-2) .

However, this deletion only removes the mapping. The following critical issues occur:
1. **Supply Mismatch**: The coins minted during the initial `ConvertERC20` remain in circulation in the `bank` module. Since the `TokenPair` is deleted, `ConvertCoin` (the path to burn these coins and release ERC-20s) will now fail because it cannot find the registered pair [4](#0-3) .
2. **Permanent Lock**: Any ERC-20 tokens previously escrowed in the module account for that pair are now permanently locked. Even if a new contract is deployed at the same address (e.g., via `CREATE2`), the `x/erc20` module provides no way to "re-link" the existing escrowed coins to the new contract, as the original `TokenPair` metadata and its relationship to the minted supply are lost.
3. **IBC Failure**: IBC transfers involving these tokens will fail during the refund process (`OnTimeoutPacket` or `OnAcknowledgementPacket`) because they rely on `ConvertCoinToERC20FromPacket`, which also requires a valid `TokenPair` to function [5](#0-4) .

### Impact Explanation
This is a **Critical** impact as it results in:
- **Irreversible accounting corruption**: The `bank` module supply remains inflated by coins that are backed by a non-existent (deleted) ERC-20 pair.
- **Permanent locking of funds**: User funds converted to Cosmos coins or held in IBC escrows become unrecoverable if the underlying ERC-20 contract is destroyed, as the module deletes the only state record (`TokenPair`) that allows for redemption.

### Likelihood Explanation
The likelihood is medium. While self-destructing a token contract is not a standard operation for reputable tokens, it is a reachable state in the EVM. A malicious or buggy contract owner could trigger this, or a contract could be designed with a lifecycle that includes self-destruction, inadvertently bricking all converted Cosmos-side assets.

### Recommendation
Instead of immediately deleting the `TokenPair` when a self-destructed contract is detected, the module should:
1. Transition the `TokenPair` to a "Deprecated" or "Internal" state that allows for one-way `ConvertCoin` (burning coins) to allow users to exit if the contract still holds funds or is redeployed.
2. If deletion is required, implement a "Supply Recovery" mechanism that burns the remaining module-held coins or allows a DAO/Governance action to recover the escrowed assets.
3. Ensure that `DeleteTokenPair` [6](#0-5)  includes logic to handle the outstanding supply associated with the `denom`.

### Proof of Concept
1. Register a native ERC-20 contract and create a `TokenPair`.
2. A user calls `MsgConvertERC20` to convert 100 Tokens into 100 Coins. The module now holds 100 Tokens in escrow, and the user has 100 Coins.
3. The ERC-20 contract is self-destructed (e.g., via a `kill()` function in the Solidity code).
4. Any user (or the original user) calls `MsgConvertERC20` for the same contract.
5. The `Keeper` executes `k.DeleteTokenPair(ctx, pair)` [7](#0-6) .
6. The user attempts to convert their 100 Coins back to Tokens using `MsgConvertCoin`.
7. The call fails at `k.MintingEnabled` because `k.GetTokenPairID(ctx, token)` returns empty, as the pair was deleted [4](#0-3) .
8. The 100 Coins are now stuck in the user's wallet (or IBC escrow), and the 100 Tokens are stuck in the `x/erc20` module account forever.

### Citations

**File:** x/erc20/keeper/msg_server.go (L44-52)
```go
		acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
		if acc == nil || !acc.HasCodeHash() {
			k.DeleteTokenPair(ctx, pair)
			k.Logger(ctx).Debug(
				"deleting selfdestructed token pair from state",
				"contract", pair.Erc20Address,
			)
			// NOTE: return nil error to persist the changes from the deletion
			return nil, nil
```

**File:** x/erc20/keeper/msg_server.go (L132-135)
```go
	// Mint coins
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}
```

**File:** x/erc20/keeper/mint.go (L29-34)
```go
	id := k.GetTokenPairID(ctx, token)
	if len(id) == 0 {
		return types.TokenPair{}, errorsmod.Wrapf(
			types.ErrTokenPairNotFound, "token '%s' not registered by id", token,
		)
	}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L200-205)
```go
	pairID := k.GetTokenPairID(ctx, data.Denom)
	pair, found := k.GetTokenPair(ctx, pairID)
	if !found {
		// no-op, token pair is not registered
		return nil
	}
```

**File:** x/erc20/keeper/token_pairs.go (L111-117)
```go
func (k Keeper) DeleteTokenPair(ctx sdk.Context, tokenPair types.TokenPair) {
	id := tokenPair.GetID()
	k.deleteTokenPair(ctx, id)
	k.deleteERC20Map(ctx, tokenPair.GetERC20Contract())
	k.deleteDenomMap(ctx, tokenPair.Denom)
	k.deleteAllowances(ctx, tokenPair.GetERC20Contract())
}
```
