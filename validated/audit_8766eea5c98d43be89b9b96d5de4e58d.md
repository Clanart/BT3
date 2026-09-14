### Title
Plot NFT buyers on secondary markets can be scammed: unclaimed p2_singleton pool rewards are not bound to Plot NFT ownership transfer - (File: `chia/pools/pool_wallet.py`)

### Summary
Chia's Plot NFT (pool singleton) accumulates farming rewards in independent `p2_singleton` coins that are logically "owned" by whoever controls the plot NFT's singleton, but claiming those rewards is a completely separate spend from any ownership transfer of the singleton itself. This mirrors the Footium bug: a "club" NFT's value (its escrowed players) is decoupled from the NFT transfer, letting a seller front-run a buyer by draining the escrow just before/at the same time the NFT changes hands.

### Finding Description
A Plot NFT's value to a buyer includes both the singleton coin itself and any unclaimed farming rewards sitting in `p2_singleton` puzzle-hash coins tied to that `launcher_id`. `pay_to_singleton_puzzle()` and `claim_p2_singleton()` [1](#0-0)  show that a `p2_singleton` coin can be claimed by *any* spend that recreates the current singleton and satisfies the required coin-announcement assertion — there is no additional binding that requires the claim to be batched atomically with a change-of-ownership spend of the singleton.

`PoolWallet.claim_pool_rewards()` independently gathers all unspent `p2_singleton` reward coins for the pool wallet and spends them alongside a self-recreating singleton spend [2](#0-1) , with no coupling to a subsequent or simultaneous transfer/travel spend that would move ownership to a new party. `create_absorb_spend()` likewise builds this pair of spends (singleton self-recreation + `p2_singleton` reward claim) independently of any singleton "travel" (leave-pool/self-pool/join-pool) transition [3](#0-2) .

Because reward-claiming and ownership-changing are two unrelated, independently-submittable spend bundles signed with the same owner key, a seller who has agreed (off-chain, e.g. via a marketplace) to sell a "loaded" Plot NFT with accrued, unclaimed rewards can simply submit a `claim_pool_rewards` transaction that drains all `p2_singleton` coins to themselves before or in the same block as completing the transfer of the singleton. The buyer receives the Plot NFT/pool singleton but none of the reward value that made it valuable — directly analogous to the Footium escrow-draining scam, where the NFT (club) and its escrowed value (players) are separable and the seller can extract the value first.

### Impact Explanation
A buyer relying on the visible unclaimed `p2_singleton` reward balance associated with a Plot NFT (a common practice when Plot NFTs with accumulated, un-swept rewards are transferred/sold) can pay for value that is unilaterally extractable by the seller beforehand. This is a concrete, spend-bundle-reachable theft of expected value/asset ownership from an unprivileged wallet-action perspective — the seller need only submit `claim_pool_rewards` ahead of/alongside the transfer.

### Likelihood Explanation
Likelihood is high whenever Plot NFTs carrying meaningful unclaimed reward balances change hands outside of a strictly on-chain-atomic mechanism (i.e., any transfer that isn't cryptographically bound to a simultaneous, mandatory reward-claim-to-buyer spend). Since `claim_pool_rewards` and singleton ownership transfer are independent code paths with no shared enforcement, any rational seller aware of pending rewards is incentivized to claim them first.

### Recommendation
Bind reward claiming to ownership transfer: require that any transfer/travel spend of the pool singleton either (a) is preceded by a mandatory claim of all outstanding `p2_singleton` rewards to the new owner's target puzzle hash as part of the same spend bundle, or (b) asserts/locks that no unclaimed `p2_singleton` coins exist for that `launcher_id` at the moment of transfer (e.g., via an announcement/assertion enforced in the transfer puzzle). Alternatively, document and enforce at the wallet/RPC layer that Plot NFT transfers must be preceded by an atomic reward sweep to the buyer's own destination puzzle hash within the same transaction, rather than relying on off-chain trust.

### Proof of Concept
1. Seller lists a Plot NFT for sale on a secondary marketplace, showcasing X accumulated but unclaimed rewards sitting in `p2_singleton` coins tied to the singleton's `launcher_id`.
2. Buyer submits payment/agrees to the sale expecting to receive both the Plot NFT and its accrued rewards.
3. Before (or in the same block as) completing the on-chain transfer of Plot NFT ownership, the seller calls `PoolWallet.claim_pool_rewards()` [2](#0-1) , which spends all currently unspent `p2_singleton` reward coins to the seller's own `target_puzzle_hash`.
4. The buyer ends up owning an "emptied" Plot NFT singleton with none of the rewards they believed they were purchasing, because claiming and transferring are unrelated, independently authorizable spends.

### Citations

**File:** chia/wallet/puzzles/singleton_top_layer_v1_1.py (L294-345)
```python
# Create a coin that a singleton can claim
def pay_to_singleton_puzzle(launcher_id: bytes32) -> Program:
    return P2_SINGLETON_MOD.curry(SINGLETON_MOD_HASH, launcher_id, SINGLETON_LAUNCHER_HASH)


# Create a coin that a singleton can claim or that can be sent to another puzzle after a specified time
def pay_to_singleton_or_delay_puzzle(launcher_id: bytes32, delay_time: uint64, delay_ph: bytes32) -> Program:
    return P2_SINGLETON_OR_DELAYED_MOD.curry(
        SINGLETON_MOD_HASH,
        launcher_id,
        SINGLETON_LAUNCHER_HASH,
        delay_time,
        delay_ph,
    )


# Solution for EITHER p2_singleton or the claiming spend case for p2_singleton_or_delayed_puzhash
def solution_for_p2_singleton(p2_singleton_coin: Coin, singleton_inner_puzhash: bytes32) -> Program:
    solution: Program = Program.to([singleton_inner_puzhash, p2_singleton_coin.name()])
    return solution


# Solution for the delayed spend case for p2_singleton_or_delayed_puzhash
def solution_for_p2_delayed_puzzle(output_amount: uint64) -> Program:
    solution: Program = Program.to([output_amount, []])
    return solution


# Get announcement conditions for singleton solution and full CoinSpend for the claimed coin
def claim_p2_singleton(
    p2_singleton_coin: Coin,
    singleton_inner_puzhash: bytes32,
    launcher_id: bytes32,
    delay_time: uint64 | None = None,
    delay_ph: bytes32 | None = None,
) -> tuple[Program, Program, CoinSpend]:
    assertion = Program.to([ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT, std_hash(p2_singleton_coin.name() + b"$")])
    announcement = Program.to([ConditionOpcode.CREATE_PUZZLE_ANNOUNCEMENT, p2_singleton_coin.name()])
    if delay_time is None or delay_ph is None:
        puzzle: Program = pay_to_singleton_puzzle(launcher_id)
    else:
        puzzle = pay_to_singleton_or_delay_puzzle(
            launcher_id,
            delay_time,
            delay_ph,
        )
    claim_coinsol = make_spend(
        p2_singleton_coin,
        puzzle,
        solution_for_p2_singleton(p2_singleton_coin, singleton_inner_puzhash),
    )
    return assertion, announcement, claim_coinsol
```

**File:** chia/pools/pool_wallet.py (L713-781)
```python
    async def claim_pool_rewards(
        self, fee: uint64, max_spends_in_tx: int | None, action_scope: WalletActionScope
    ) -> None:
        # Search for p2_puzzle_hash coins, and spend them with the singleton
        if await self.have_unconfirmed_transaction():
            raise ValueError(
                "Cannot claim due to unconfirmed transaction. If this is stuck, delete the unconfirmed transaction."
            )

        if max_spends_in_tx is None:
            max_spends_in_tx = self.DEFAULT_MAX_CLAIM_SPENDS
        elif max_spends_in_tx <= 0:
            self.log.info(f"Bad max_spends_in_tx value of {max_spends_in_tx}. Set to {self.DEFAULT_MAX_CLAIM_SPENDS}.")
            max_spends_in_tx = self.DEFAULT_MAX_CLAIM_SPENDS

        unspent_coin_records = await self.wallet_state_manager.coin_store.get_unspent_coins_for_wallet(self.wallet_id)
        if len(unspent_coin_records) == 0:
            raise ValueError("Nothing to claim, no transactions to p2_singleton_puzzle_hash")
        farming_rewards: list[TransactionRecord] = await self.wallet_state_manager.tx_store.get_farming_rewards()
        coin_to_height_farmed: dict[Coin, uint32] = {}
        for tx_record in farming_rewards:
            height_farmed: uint32 | None = tx_record.height_farmed(
                self.wallet_state_manager.constants.GENESIS_CHALLENGE
            )
            assert height_farmed is not None
            coin_to_height_farmed[tx_record.additions[0]] = height_farmed
        history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
        assert len(history) > 0
        delayed_seconds, delayed_puzhash = get_delayed_puz_info_from_launcher_spend(history[0][1])
        current_state: PoolWalletInfo = await self.get_current_state()
        last_solution: CoinSpend = history[-1][1]

        all_spends: list[CoinSpend] = []
        total_amount = 0

        # The coins being claimed are gathered into the `SpendBundle`, :absorb_spend:
        # We use an announcement in the fee spend to ensure that the claim spend is spent in the same block as the fee
        # We only need to do this for one of the coins, because each `SpendBundle` can only be spent as a unit

        first_coin_record = None
        for coin_record in unspent_coin_records:
            if coin_record.coin not in coin_to_height_farmed:
                continue
            if first_coin_record is None:
                first_coin_record = coin_record
            if len(all_spends) >= max_spends_in_tx:
                # Limit the total number of spends, so the SpendBundle fits into the block
                self.log.info(f"pool wallet truncating absorb to {max_spends_in_tx} spends to fit into block")
                print(f"pool wallet truncating absorb to {max_spends_in_tx} spends to fit into block")
                break
            absorb_spend: list[CoinSpend] = create_absorb_spend(
                last_solution,
                current_state.current,
                current_state.launcher_coin,
                coin_to_height_farmed[coin_record.coin],
                self.wallet_state_manager.constants.GENESIS_CHALLENGE,
                delayed_seconds,
                delayed_puzhash,
            )
            last_solution = absorb_spend[0]
            all_spends += absorb_spend
            total_amount += coin_record.coin.amount
            self.log.info(
                f"Farmer coin: {coin_record.coin} {coin_record.coin.name()} {coin_to_height_farmed[coin_record.coin]}"
            )
        if len(all_spends) == 0 or first_coin_record is None:
            raise ValueError("Nothing to claim, no unspent coinbase rewards")

        claim_spend = WalletSpendBundle(all_spends, G2Element())
```

**File:** chia/pools/pool_puzzles.py (L252-308)
```python
def create_absorb_spend(
    last_coin_spend: CoinSpend,
    current_state: PoolState,
    launcher_coin: Coin,
    height: uint32,
    genesis_challenge: bytes32,
    delay_time: uint64,
    delay_ph: bytes32,
) -> list[CoinSpend]:
    inner_puzzle: Program = pool_state_to_inner_puzzle(
        current_state, launcher_coin.name(), genesis_challenge, delay_time, delay_ph
    )
    reward_amount: uint64 = calculate_pool_reward(height)
    if is_pool_member_inner_puzzle(inner_puzzle):
        # inner sol is (spend_type, pool_reward_amount, pool_reward_height, extra_data)
        inner_sol: Program = Program.to([reward_amount, height])
    elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
        # inner sol is (spend_type, destination_puzhash, pool_reward_amount, pool_reward_height, extra_data)
        inner_sol = Program.to([0, reward_amount, height])
    else:
        raise ValueError
    # full sol = (parent_info, my_amount, inner_solution)
    coin: Coin | None = get_most_recent_singleton_coin_from_coin_spend(last_coin_spend)
    assert coin is not None

    if coin.parent_coin_info == launcher_coin.name():
        parent_info: Program = Program.to([launcher_coin.parent_coin_info, launcher_coin.amount])
    else:
        p = Program.from_bytes(bytes(last_coin_spend.puzzle_reveal))
        last_coin_spend_inner_puzzle: Program | None = get_inner_puzzle_from_puzzle(p)
        assert last_coin_spend_inner_puzzle is not None
        parent_info = Program.to(
            [
                last_coin_spend.coin.parent_coin_info,
                last_coin_spend_inner_puzzle.get_tree_hash(),
                last_coin_spend.coin.amount,
            ]
        )
    full_solution: SerializedProgram = SerializedProgram.to([parent_info, last_coin_spend.coin.amount, inner_sol])
    full_puzzle: SerializedProgram = create_full_puzzle(inner_puzzle, launcher_coin.name()).to_serialized()
    assert coin.puzzle_hash == full_puzzle.get_tree_hash()

    reward_parent: bytes32 = pool_parent_id(height, genesis_challenge)
    p2_singleton_puzzle = create_p2_singleton_puzzle(
        SINGLETON_MOD_HASH, launcher_coin.name(), delay_time, delay_ph
    ).to_serialized()
    reward_coin: Coin = Coin(reward_parent, p2_singleton_puzzle.get_tree_hash(), reward_amount)
    p2_singleton_solution = SerializedProgram.to([inner_puzzle.get_tree_hash(), reward_coin.name()])
    assert p2_singleton_puzzle.get_tree_hash() == reward_coin.puzzle_hash
    assert full_puzzle.get_tree_hash() == coin.puzzle_hash
    assert get_inner_puzzle_from_puzzle(Program.from_bytes(bytes(full_puzzle))) is not None

    coin_spends = [
        CoinSpend(coin, full_puzzle, full_solution),
        CoinSpend(reward_coin, p2_singleton_puzzle, p2_singleton_solution),
    ]
    return coin_spends
```
