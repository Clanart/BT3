No vulnerability found for this question.

The reported bug class—share-price manipulation of an ERC-20-style lending pool via direct "donation" transfers that inflate a mutable `balanceOf(address(this))` used in a `deposit()`/`withdraw()` share-ratio formula—has no structural analog in `chia-blockchain`. Chia's coin-set model does not have any component that computes ownership shares from a token's `balanceOf`; value is tracked as discrete, uniquely-identified coins consumed/created by spends, not as a mutable balance variable that an attacker can inflate by sending assets to a contract address.

I looked specifically at the closest conceptual match, the pooling/plotnft subsystem (`chia/pools/pool_wallet.py`, `chia/pools/pool_puzzles.py`, `chia/pools/pool_wallet_info.py`), since it's the module literally called "pools." Its "pool rewards" are singleton-tracked coin absorption (`create_absorb_spend()`, `PoolWallet.claim_pool_rewards()`), not a shares-per-deposit accounting scheme [1](#0-0) . Rewards are claimed by spending specific, individually-identified p2-singleton reward coins tied to consensus coinbase rules (`pool_parent_id`, `calculate_pool_reward`), so there is no `totalSupply`/`balanceOf` ratio that a third party can dilute by donating tokens directly to a puzzle hash [2](#0-1) . Similarly, `PoolWallet.get_confirmed_balance()` sums specific coinbase-flagged unspent coin records rather than deriving a share of a shared pool balance [3](#0-2) .

I also checked CAT/wallet coin selection and balance code (`chia/wallet/coin_selection.py`, `chia/wallet/wallet.py`) — these operate on discrete coin sets selected to meet an exact spend amount, with no share-minting-by-ratio logic that a donation could skew [4](#0-3) . No CAT, CR-CAT, VC, NFT/DID, offer, clawback, or DL code path implements a deposit-for-shares vault pattern either.

Since the underlying vulnerability class requires a mutable "total pool balance" state variable read via something like `balanceOf(address(this))` that can be inflated by unsolicited transfers — a concept that doesn't exist in Chia's coin-and-puzzle model — there is no reachable analog for a spend-bundle submitter, wallet user, or pool participant to exploit here.

### Citations

**File:** chia/pools/pool_wallet.py (L391-443)
```python
    @staticmethod
    async def create_new_pool_wallet_transaction(
        wallet_state_manager: Any,
        main_wallet: Wallet,
        initial_target_state: PoolState,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        p2_singleton_delay_time: uint64 | None = None,
        p2_singleton_delayed_ph: bytes32 | None = None,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> tuple[bytes32, bytes32]:
        """
        A "plot NFT", or pool wallet, represents the idea of a set of plots that all pay to
        the same pooling puzzle. This puzzle is a `chia singleton` that is
        parameterized with a public key controlled by the user's wallet
        (a `smart coin`). It contains an inner puzzle that can switch between
        paying block rewards to a pool, or to a user's own wallet.

        Call under the wallet state manager lock
        """
        standard_wallet = main_wallet

        if p2_singleton_delayed_ph is None:
            p2_singleton_delayed_ph = await action_scope.get_puzzle_hash(wallet_state_manager)
        if p2_singleton_delay_time is None:
            p2_singleton_delay_time = uint64(604800)

        unspent_records = await wallet_state_manager.coin_store.get_unspent_coins_for_wallet(standard_wallet.wallet_id)
        balance = await standard_wallet.get_confirmed_balance(unspent_records)
        if balance < PoolWallet.MINIMUM_INITIAL_BALANCE:
            raise ValueError("Not enough balance in main wallet to create a managed plotting pool.")
        if balance < PoolWallet.MINIMUM_INITIAL_BALANCE + fee:
            raise ValueError(f"Not enough balance in main wallet to create a managed plotting pool with fee {fee}.")

        # Verify Parameters - raise if invalid
        PoolWallet._verify_initial_target_state(initial_target_state)

        _singleton_puzzle_hash, launcher_coin_id = await PoolWallet.generate_launcher_spend(
            standard_wallet,
            uint64(1),
            fee,
            initial_target_state,
            wallet_state_manager.constants.GENESIS_CHALLENGE,
            p2_singleton_delay_time,
            p2_singleton_delayed_ph,
            action_scope,
            extra_conditions=extra_conditions,
        )

        p2_singleton_puzzle_hash: bytes32 = launcher_id_to_p2_puzzle_hash(
            launcher_coin_id, p2_singleton_delay_time, p2_singleton_delayed_ph
        )

```

**File:** chia/pools/pool_wallet.py (L859-868)
```python
    async def get_confirmed_balance(self, record_list: object | None = None) -> uint128:
        amount: uint128 = uint128(0)
        if (await self.get_current_state()).current.state == SELF_POOLING.value:
            unspent_coin_records: list[WalletCoinRecord] = list(
                await self.wallet_state_manager.coin_store.get_unspent_coins_for_wallet(self.wallet_id)
            )
            for record in unspent_coin_records:
                if record.coinbase:
                    amount = uint128(amount + record.coin.amount)
        return amount
```

**File:** .cursor/context/pools.md (L54-55)
```markdown
- Absorb spends reconstruct reward coins using `pool_parent_id(height, genesis_challenge)` and `calculate_pool_reward(height)`. This is intentionally tied to consensus coinbase rules; changing reward schedule or parent-id logic without updating absorb tests can strand claimable rewards.
- `PoolWallet.claim_pool_rewards()` maps wallet farming reward transaction records back to block heights, then uses those heights to build reward coin spends. If reward detection or transaction-record height derivation changes in wallet code, pool reward claims can break even when pool puzzles are untouched.
```

**File:** chia/wallet/coin_selection.py (L22-39)
```python
    """
    Returns a set of coins that can be used for generating a new transaction.
    """
    if amount > spendable_amount:
        error_msg = (
            f"Can't select amount higher than our spendable balance.  Amount: {amount}, spendable: {spendable_amount}"
        )
        log.warning(error_msg)
        raise ValueError(error_msg)

    log.debug(f"About to select coins for amount {amount}")

    max_num_coins = 500
    confirmed_spendable_coins = {cr for cr in spendable_coins if cr.coin.name() not in unconfirmed_removals}
    valid_spendable_coins: list[Coin] = list(
        coin_selection_config.filter_coins({cr.coin for cr in confirmed_spendable_coins})
    )
    sum_spendable_coins = sum(coin.amount for coin in valid_spendable_coins)
```
