### Title
Token-pair deletion on self-destructed native ERC20 contract permanently locks user bank balances - (`x/erc20/keeper/msg_server.go`)

### Summary
The external `removePool()` bug class maps to `x/erc20` token-pair lifecycle handling. When a native-ERC20 token pair is deleted because the underlying contract was self-destructed, the module removes the pair mapping but does not first redeem or clear outstanding bank balances of the paired Cosmos coin. After deletion, `MintingEnabled` rejects any conversion attempt for that denom/contract, leaving users with non-spendable, non-convertible bank coins that are still counted in their balances.

### Finding Description
In `x/erc20/keeper/msg_server.go`, both `ConvertERC20` and `ConvertCoin` detect a self-destructed native ERC20 contract and call `DeleteTokenPair` before doing any balance checks or conversion logic:

```go
if pair.IsNativeERC20() {
    acc := k.evmKeeper.GetAccountWithoutBalance(ctx, pair.GetERC20Contract())
    if acc == nil || !acc.HasCodeHash() {
        k.DeleteTokenPair(ctx, pair)
        ...
        return nil, nil
    }
    ...
}
```

`DeleteTokenPair` removes the token-pair record, the ERC20→pair index, the denom→pair index, and allowances, but it does not check whether any user still holds the Cosmos coin `pair.Denom` in `x/bank`. Once the mapping is gone, `MintingEnabled` in `x/erc20/keeper/mint.go` returns `ErrTokenPairNotFound` for any subsequent `MsgConvertCoin` or `MsgConvertERC20` because `GetTokenPairID` now returns empty bytes:

```go
id := k.GetTokenPairID(ctx, token)
if len(id) == 0 {
    return types.TokenPair{}, errorsmod.Wrapf(
        types.ErrTokenPairNotFound, "token '%s' not registered by id", token,
    )
}
```

The bank coins themselves are not burned or migrated, so the user’s `x/bank` balance still reports the amount, but there is no code path that can convert or redeem it.

### Impact Explanation
This is a Critical permanent freezing of user funds:

- A user who previously called `ConvertERC20` holds the Cosmos representation of a native ERC20 token.
- The ERC20 contract owner self-destructs the contract (unprivileged relative to the contract deployer, via ordinary EVM execution).
- The next `ConvertCoin` or `ConvertERC20` transaction triggers `DeleteTokenPair`, after which the mapping is gone.
- The user’s bank balance of `pair.Denom` remains, but every conversion entry point is now gated by `MintingEnabled` and fails with `ErrTokenPairNotFound`.
- There is no recovery function, no re-registration path for the same denom (registration would fail because the contract address is fixed by `CreateDenom`), and no admin/governance message to un-delete the pair. The funds are permanently locked in the user’s bank balance.

This matches the allowed impact gate: *Critical permanent freezing, locking, theft, or unauthorized extraction of user funds, contract balances, escrowed assets, staking/distribution value, or token-pair-backed balances.*

### Likelihood Explanation
High. Any registered native ERC20 contract can be self-destructed by its deployer through standard EVM means. The deletion is triggered automatically on the next conversion attempt, which can be initiated by any user (including the attacker) because `MsgConvertERC20`/`MsgConvertCoin` are public messages. No privileged role is required to trigger the deletion; only the contract self-destruct is owner-controlled, and the resulting state corruption is permanent and affects all holders.

### Recommendation
Before deleting a token pair, verify that no outstanding bank supply of `pair.Denom` exists, or provide a recovery path:

1. In `ConvertERC20`/`ConvertCoin`, before calling `DeleteTokenPair`, check `bankKeeper.GetSupply(ctx, pair.Denom).IsZero()`. If supply is non-zero, do not delete the pair; instead return an error so users can still convert back.
2. Alternatively, implement a governance-only `RecoverTokenPair` or `ForceConvertCoinToERC20` function that can redeem the bank coins even when the contract is gone, by burning the bank coins and crediting an equivalent value or marking them as lost.

### Proof of Concept
1. Deploy a native ERC20 contract and register it via `MsgRegisterERC20`, creating a token pair `(contractAddr, denom)`.
2. User A mints 100 ERC20 tokens and calls `MsgConvertERC20` to receive 100 `denom` bank coins.
3. The contract deployer calls `selfdestruct` on the contract.
4. User A attempts `MsgConvertCoin` of 100 `denom` to get ERC20 tokens back.
5. `ConvertCoin` sees `GetAccountWithoutBalance(contractAddr)` returns `nil` (or no code hash), calls `DeleteTokenPair`, and returns `nil, nil`.
6. The token-pair mapping is removed.
7. Any further `MsgConvertCoin` or `MsgConvertERC20` for that denom/contract fails in `MintingEnabled` with `ErrTokenPairNotFound`.
8. User A still has 100 `denom` in `x/bank`, but it can never be converted, transferred via IBC as the ERC20 representation, or redeemed.