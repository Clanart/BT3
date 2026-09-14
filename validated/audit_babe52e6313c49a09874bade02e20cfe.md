### Title
Missing zero-address validation on pool `target_puzzle_hash` allows permanent loss of pool rewards - (File: chia/pools/pool_wallet.py)

### Summary
`PoolWallet._verify_pool_state()` validates several fields of a `PoolState` (pool URL, relative lock height, protocol version) before it is accepted as a wallet's pool singleton state, but never checks that `target_puzzle_hash` — the final destination address that all pool/self-pooled rewards are paid to — is non-zero. This is the same bug class as the reported `AccountantDelegate.initialize()` finding: a critical, hard-to-change destination-address field lacks a zero-address guard.

### Finding Description
`PoolState.target_puzzle_hash` is documented as "either the pool address, or the self-pooling address that pool rewards will be paid to" and is explicitly distinguished from the transient `p2_singleton` address — it is the durable final payout destination. [1](#0-0) 

The only validation gate for a `PoolState` before it becomes the wallet's target/current state is `_verify_pool_state()`: [2](#0-1) 

It checks only that `target_puzzle_hash is None`, protocol version compatibility, and then dispatches to `_verify_self_pooled()` / `_verify_pooling_state()`: [3](#0-2) 

Neither of these sub-validators, nor `_verify_pool_state`, ever compares `target_puzzle_hash` against `bytes32.zeros` (the burn/zero puzzle hash). Consequently, a `PoolState`/`NewPoolWalletInitialTargetState` with `target_puzzle_hash = bytes32.zeros` passes validation in `_verify_initial_target_state()` and is accepted by `create_new_pool_wallet_transaction()`. [4](#0-3) 

The FARMING_TO_POOL creation path in the CLI even pulls `target_puzzle_hash` directly from an external, unauthenticated HTTP response (`create_pool_args()` → `json_dict["target_puzzle_hash"]`) with no zero-address sanity check before it is passed into wallet RPC as the reward destination: [5](#0-4) 

Existing tests even demonstrate a zero `target_puzzle_hash` being set on pool state/config without any rejection (`pool_ph = bytes32.zeros` used directly as `target_puzzlehash` in `pw_join_pool`, and `pool_config.target_puzzle_hash = bytes32(32 * b"0")` accepted in CLI tests), confirming there is no protective check anywhere in this code path. [6](#0-5) [7](#0-6) 

Once a singleton travel/absorb spend commits this `target_puzzle_hash` on-chain (embedded in the `PoolState` inside the launcher/travel solution), `PoolWallet.claim_pool_rewards()` / the absorb-spend builder pays the wallet's claimed pool rewards to exactly this puzzle hash — as `pool_wallet.md`/source describes, absorb spends record an outgoing transaction "paying the absorbed amount to the current target puzzle hash." [8](#0-7) 

If `target_puzzle_hash` is the zero puzzle hash (or any other address nobody controls), every future absorbed pool/self-pooling reward for that singleton is sent to an unspendable/uncontrolled destination.

### Impact Explanation
This mirrors the confirmed Medium-severity C4 finding exactly: the risk is not simply "no check," but that (1) the field, once committed on-chain via a singleton spend, is expensive/impossible to correct without another full travel transaction (and in the case of `SELF_POOLING`→claim flow, prior claims are already unrecoverable), and (2) an incorrect value causes concrete loss of funds — every pool reward the plot-NFT singleton claims is paid irrevocably to the zero/burn puzzle hash rather than to the user or their pool. Because the value can originate from an external pool server response (`create_pool_args`) as well as from user/CLI/RPC input, it is reachable by a normal wallet user or by a pool operator returning a malformed `target_puzzle_hash`, without needing any special privilege.

### Likelihood Explanation
Likelihood is moderate: a user must go through `create_new_pool_wallet_transaction` or `pw_join_pool` with an attacker-influenced or accidentally-zero `target_puzzle_hash` (e.g., a malicious/misconfigured pool's `/pool_info` response, or a CLI/RPC caller passing a zero address), which chia-blockchain's own validation never rejects. This is analogous to, and slightly weaker than, the original report (which required only a single `initialize()` call); here it requires reaching pool-wallet creation/join flows, but no additional privilege is needed beyond a normal wallet action, and the value is not always fully user-controlled (it can come from a third-party pool endpoint).

### Recommendation
Add an explicit check in `PoolWallet._verify_pool_state()` (or in `NewPoolWalletInitialTargetState.__post_init__` / `initial_pool_state_from_dict`) that rejects `target_puzzle_hash == bytes32.zeros` (and any other well-known burn addresses) before the state is accepted for launcher creation, join-pool, or leave-pool transactions. Additionally, `create_pool_args()` in `chia/cmds/plotnft_funcs.py` should validate that the pool-supplied `target_puzzle_hash` is non-zero before it is used to construct a `NewPoolWalletInitialTargetState`.

### Proof of Concept
1. Call `create_new_pool_wallet_transaction()` with `initial_target_state.target_puzzle_hash = bytes32.zeros` and `state = FARMING_TO_POOL` (or use `pw_join_pool` with `target_puzzlehash=bytes32.zeros`, as already done in `test_pool_rpc.py::test_self_pooling_to_pooling`, line 949/1007). [9](#0-8) 
2. `PoolWallet._verify_pool_state()` only checks `target_puzzle_hash is None`, not zero-value, so validation passes. [10](#0-9) 
3. The launcher/travel spend commits this `PoolState` on-chain; subsequent `claim_pool_rewards()`/absorb spends pay all future pool rewards for this singleton to `bytes32.zeros`, permanently burning them.

**Note on confidence**: I was unable to fully inspect `pw_join_pool`'s RPC handler implementation in `chia/wallet/wallet_rpc_api.py` (the read tool returned only line 1 due to index truncation) to confirm whether a zero-address check might exist there specifically; based on all other validation paths (`_verify_pool_state`, `NewPoolWalletInitialTargetState.__post_init__`, and existing tests that successfully set zero addresses without rejection), no such check appears to exist anywhere in the pool-wallet stack. A Devin session with full file access could confirm this definitively.

### Citations

**File:** chia/pools/pool_wallet_info.py (L43-61)
```python
class PoolState(Streamable):
    """
    `PoolState` is a type that is serialized to the blockchain to track the state of the user's pool singleton
    `target_puzzle_hash` is either the pool address, or the self-pooling address that pool rewards will be paid to.
    `target_puzzle_hash` is NOT the p2_singleton puzzle that block rewards are sent to.
    The `p2_singleton` address is the initial address, and the `target_puzzle_hash` is the final destination.
    `relative_lock_height` is zero when in SELF_POOLING state
    """

    version: uint8
    state: uint8  # PoolSingletonState
    # `target_puzzle_hash`: A puzzle_hash we pay to
    # When self-farming, this is a main wallet address
    # When farming-to-pool, the pool sends this to the farmer during pool protocol setup
    target_puzzle_hash: bytes32  # TODO: rename target_puzzle_hash -> pay_to_address
    # owner_pubkey is set by the wallet, once
    owner_pubkey: G1Element
    pool_url: str | None
    relative_lock_height: uint32
```

**File:** chia/pools/pool_wallet.py (L136-162)
```python
    def _verify_self_pooled(cls, state: PoolState) -> str | None:
        err = ""
        if state.pool_url not in {None, ""}:
            err += " Unneeded pool_url for self-pooling"

        if state.relative_lock_height != 0:
            err += " Incorrect relative_lock_height for self-pooling"

        return None if err == "" else err

    @classmethod
    def _verify_pooling_state(cls, state: PoolState) -> str | None:
        err = ""
        if state.relative_lock_height < cls.MINIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is less than recommended minimum ({cls.MINIMUM_RELATIVE_LOCK_HEIGHT})"
            )
        elif state.relative_lock_height > cls.MAXIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is greater than recommended maximum ({cls.MAXIMUM_RELATIVE_LOCK_HEIGHT})"
            )

        if state.pool_url in {None, ""}:
            err += " Empty pool url in pooling state"
        return err
```

**File:** chia/pools/pool_wallet.py (L164-181)
```python
    @classmethod
    def _verify_pool_state(cls, state: PoolState) -> str | None:
        if state.target_puzzle_hash is None:
            return "Invalid puzzle_hash"

        if state.version > POOL_PROTOCOL_VERSION:
            return (
                f"Detected pool protocol version {state.version}, which is "
                f"newer than this wallet's version ({POOL_PROTOCOL_VERSION}). Please upgrade "
                f"to use this pooling wallet"
            )

        if state.state == PoolSingletonState.SELF_POOLING.value:
            return cls._verify_self_pooled(state)
        elif state.state in {PoolSingletonState.FARMING_TO_POOL.value, PoolSingletonState.LEAVING_POOL.value}:
            return cls._verify_pooling_state(state)
        else:
            return "Internal Error"
```

**File:** chia/pools/pool_wallet.py (L391-438)
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
```

**File:** chia/cmds/plotnft_funcs.py (L59-106)
```python
async def create_pool_args(pool_url: str) -> dict[str, Any]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{pool_url}/pool_info", ssl=ssl_context_for_root(get_mozilla_ca_crt())) as response:
                if response.ok:
                    json_dict: dict[str, Any] = json.loads(await response.text())
                else:
                    raise ValueError(f"Response from {pool_url} not OK: {response.status}")
    except Exception as e:
        raise ValueError(f"Error connecting to pool {pool_url}: {e}")

    if json_dict["relative_lock_height"] > 1000:
        raise ValueError("Relative lock height too high for this pool, cannot join")
    if json_dict["protocol_version"] not in {1, 2}:
        raise ValueError(f"Incorrect version: {json_dict['protocol_version']}, should be 1 or 2")

    header_msg = f"\n---- Pool parameters fetched from {pool_url} ----"
    print(header_msg)
    pprint(json_dict)
    print("-" * len(header_msg))
    return json_dict


async def create(
    wallet_info: WalletClientInfo,
    pool_url: str | None,
    state: str,
    fee: uint64,
    *,
    prompt: bool,
    version: int,
) -> None:
    target_puzzle_hash: bytes32 | None
    # Could use initial_pool_state_from_dict to simplify
    if state == "SELF_POOLING":
        pool_url = None
        relative_lock_height = None
        target_puzzle_hash = None  # wallet will fill this in
        pool_memoization = Program.to(None)
    elif state == "FARMING_TO_POOL":
        enforce_https = wallet_info.config["selected_network"] == "mainnet"
        assert pool_url is not None
        if enforce_https and not pool_url.startswith("https://"):
            raise CliRpcConnectionError(f"Pool URLs must be HTTPS on mainnet {pool_url}.")
        json_dict = await create_pool_args(pool_url)
        relative_lock_height = json_dict["relative_lock_height"]
        target_puzzle_hash = bytes32.from_hexstr(json_dict["target_puzzle_hash"])
        pool_memoization = Program.fromhex(json_dict.get("pool_memoization", "80"))
```

**File:** chia/_tests/pools/test_pool_rpc.py (L948-1013)
```python
        full_node_api, wallet_node, _our_ph, _total_block_rewards, client = setup
        pool_ph = bytes32.zeros

        assert wallet_node._wallet_state_manager is not None

        summaries_response = await client.get_wallets(GetWallets(type=uint16(WalletType.POOLING_WALLET)))
        assert len(summaries_response.wallets) == 0

        create_response_1 = await client.create_new_wallet(
            CreateNewWallet(
                wallet_type=CreateNewWalletType.POOL_WALLET,
                initial_target_state=NewPoolWalletInitialTargetState(
                    state="SELF_POOLING",
                ),
                mode=WalletCreationMode.NEW,
                fee=fee,
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )
        await full_node_api.wait_transaction_records_entered_mempool(records=create_response_1.transactions)
        await full_node_api.wait_for_wallet_synced(wallet_node=wallet_node, timeout=20)
        create_response_2 = await client.create_new_wallet(
            CreateNewWallet(
                wallet_type=CreateNewWalletType.POOL_WALLET,
                initial_target_state=NewPoolWalletInitialTargetState(
                    state="SELF_POOLING",
                ),
                mode=WalletCreationMode.NEW,
                fee=fee,
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )

        for r in create_response_1.transactions[0].removals:
            assert r not in create_response_2.transactions[0].removals

        await full_node_api.process_transaction_records(records=create_response_2.transactions)

        assert not full_node_api.txs_in_mempool(txs=create_response_1.transactions)
        await full_node_api.wait_for_wallet_synced(wallet_node=wallet_node, timeout=20)

        summaries_response = await client.get_wallets(GetWallets(type=uint16(WalletType.POOLING_WALLET)))
        assert len(summaries_response.wallets) == 2
        wallet_id: int = summaries_response.wallets[0].id
        wallet_id_2: int = summaries_response.wallets[1].id
        status: PoolWalletInfo = (await client.pw_status(PWStatus(wallet_id=uint32(wallet_id)))).state
        status_2: PoolWalletInfo = (await client.pw_status(PWStatus(wallet_id=uint32(wallet_id_2)))).state

        assert status.current.state == PoolSingletonState.SELF_POOLING.value
        assert status_2.current.state == PoolSingletonState.SELF_POOLING.value
        assert status.target is None
        assert status_2.target is None

        await full_node_api.wait_for_wallet_synced(wallet_node=wallet_node, timeout=20)
        join_pool = await client.pw_join_pool(
            PWJoinPool(
                wallet_id=uint32(wallet_id),
                target_puzzlehash=pool_ph,
                pool_url="https://pool.example.com",
                relative_lock_height=uint32(10),
                fee=uint64(fee),
                push=True,
            ),
            DEFAULT_TX_CONFIG,
```

**File:** chia/_tests/pools/test_pool_cmdline.py (L1107-1115)
```python
    with PoolingShareState.acquire(
        root_path=root_path, p2_singleton_puzzle_hash=pw_info.p2_singleton_puzzle_hash
    ) as pool_config:
        pool_config.launcher_id = pw_info.launcher_id
        pool_config.pool_url = "http://pool.example.com"
        pool_config.payout_instructions = zero_address
        pool_config.target_puzzle_hash = bytes32(32 * b"0")
        pool_config.owner_public_key = G1Element()

```

**File:** .cursor/context/pools.md (L46-46)
```markdown
- Claiming self-pooled rewards scans this pool wallet's unspent reward coins, filters to known farming rewards, builds repeated absorb spends that advance the singleton tip while consuming p2-singleton reward coins, optionally adds a standard-wallet fee spend tied by a coin announcement, and records an outgoing transaction paying the absorbed amount to the current target puzzle hash.
```
