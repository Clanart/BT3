import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
# todo: the path from https://github.com/Chia-Network/chia-blockchain
SOURCE_REPO = "Chia-Network/chia-blockchain"
# todo: the name of the repository
REPO_NAME = "chia-blockchain"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    # =================================================================================
    # Spend bundle admission: mempool, cost accounting, dedup/fast-forward, fees
    # =================================================================================
    "chia/full_node/mempool_manager.py",
    "chia/full_node/mempool.py",
    "chia/full_node/eligible_coin_spends.py",
    "chia/full_node/pending_tx_cache.py",
    "chia/full_node/tx_processing_queue.py",
    "chia/full_node/bundle_tools.py",
    "chia/full_node/subscriptions.py",
    "chia/full_node/hint_management.py",
    "chia/full_node/hint_store.py",
    "chia/full_node/fee_estimation.py",
    "chia/full_node/fee_estimator.py",
    "chia/full_node/fee_estimator_constants.py",
    "chia/full_node/fee_estimator_interface.py",
    "chia/full_node/fee_tracker.py",
    "chia/full_node/fee_history.py",
    "chia/full_node/fee_estimate_store.py",
    "chia/full_node/bitcoin_fee_estimator.py",
    "chia/types/mempool_item.py",
    "chia/types/internal_mempool_item.py",
    "chia/types/mempool_inclusion_status.py",
    "chia/types/mempool_submission_status.py",
    "chia/types/generator_types.py",
    "chia/types/clvm_cost.py",
    "chia/types/fee_rate.py",
    "chia/types/mojos.py",
    "chia/types/coin_spend.py",
    "chia/types/condition_opcodes.py",
    "chia/types/condition_with_args.py",

    # =================================================================================
    # CLVM execution, condition parsing, coin identity and puzzle hashing
    # =================================================================================
    "chia/types/blockchain_format/program.py",
    "chia/types/blockchain_format/serialized_program.py",
    "chia/types/blockchain_format/coin.py",
    "chia/types/blockchain_format/tree_hash.py",
    "chia/consensus/condition_tools.py",
    "chia/consensus/condition_costs.py",
    "chia/consensus/generator_tools.py",
    "chia/consensus/get_block_generator.py",
    "chia/wallet/conditions.py",
    "chia/wallet/uncurried_puzzle.py",
    "chia/wallet/util/compute_additions.py",
    "chia/wallet/util/compute_hints.py",
    "chia/wallet/util/compute_memos.py",
    "chia/wallet/util/curry_and_treehash.py",
    "chia/wallet/puzzles/load_clvm.py",
    "chia/wallet/puzzles/puzzle_utils.py",
    "chia/wallet/puzzles/condition_codes.clib",
    "chia/wallet/puzzles/curry.clib",
    "chia/wallet/puzzles/utility_macros.clib",

    # =================================================================================
    # Transaction block body validation, coin set state and node tx entrypoints
    # =================================================================================
    "chia/consensus/block_body_validation.py",
    "chia/consensus/blockchain.py",
    "chia/consensus/blockchain_interface.py",
    "chia/consensus/block_creation.py",
    "chia/consensus/block_record.py",
    "chia/consensus/full_block_to_block_record.py",
    "chia/consensus/prev_transaction_block.py",
    "chia/consensus/multiprocess_validation.py",
    "chia/consensus/augmented_chain.py",
    "chia/consensus/coin_store_protocol.py",
    "chia/consensus/coinbase.py",
    "chia/consensus/block_rewards.py",
    "chia/consensus/constants.py",
    "chia/consensus/default_constants.py",
    "chia/consensus/find_fork_point.py",
    "chia/full_node/check_fork_next_block.py",
    "chia/full_node/coin_store.py",
    "chia/full_node/block_store.py",
    "chia/full_node/full_block_utils.py",
    "chia/full_node/hard_fork_utils.py",
    "chia/full_node/full_node.py",
    "chia/full_node/full_node_api.py",
    "chia/full_node/full_node_rpc_api.py",
    "chia/types/block_protocol.py",

    # =================================================================================
    # Standard puzzles, singleton lineage and generic driver dispatch
    # =================================================================================
    "chia/wallet/puzzles/p2_delegated_puzzle_or_hidden_puzzle.py",
    "chia/wallet/puzzles/p2_conditions.py",
    "chia/wallet/puzzles/p2_delegated_conditions.py",
    "chia/wallet/puzzles/p2_delegated_puzzle.py",
    "chia/wallet/puzzles/p2_m_of_n_delegate_direct.py",
    "chia/wallet/puzzles/p2_puzzle_hash.py",
    "chia/wallet/puzzles/singleton_top_layer.py",
    "chia/wallet/puzzles/singleton_top_layer_v1_1.py",
    "chia/wallet/puzzles/tails.py",
    "chia/wallet/singleton.py",
    "chia/wallet/singleton_record.py",
    "chia/wallet/lineage_proof.py",
    "chia/wallet/outer_puzzles.py",
    "chia/wallet/puzzle_drivers.py",
    "chia/wallet/driver_protocol.py",
    "chia/wallet/util/merkle_tree.py",
    "chia/wallet/util/merkle_utils.py",

    # =================================================================================
    # CAT, revocable CAT, CR-CAT and verifiable credential value flow
    # =================================================================================
    "chia/wallet/cat_wallet/cat_wallet.py",
    "chia/wallet/cat_wallet/cat_utils.py",
    "chia/wallet/cat_wallet/cat_outer_puzzle.py",
    "chia/wallet/cat_wallet/cat_info.py",
    "chia/wallet/cat_wallet/cat_constants.py",
    "chia/wallet/cat_wallet/lineage_store.py",
    "chia/wallet/cat_wallet/r_cat_wallet.py",
    "chia/wallet/vc_wallet/cr_cat_drivers.py",
    "chia/wallet/vc_wallet/cr_cat_wallet.py",
    "chia/wallet/vc_wallet/cr_outer_puzzle.py",
    "chia/wallet/vc_wallet/vc_drivers.py",
    "chia/wallet/vc_wallet/vc_wallet.py",
    "chia/wallet/vc_wallet/vc_store.py",

    # =================================================================================
    # NFT and DID ownership, transfer programs and metadata authority
    # =================================================================================
    "chia/wallet/nft_wallet/nft_wallet.py",
    "chia/wallet/nft_wallet/nft_puzzles.py",
    "chia/wallet/nft_wallet/nft_puzzle_utils.py",
    "chia/wallet/nft_wallet/nft_info.py",
    "chia/wallet/nft_wallet/uncurry_nft.py",
    "chia/wallet/nft_wallet/ownership_outer_puzzle.py",
    "chia/wallet/nft_wallet/singleton_outer_puzzle.py",
    "chia/wallet/nft_wallet/metadata_outer_puzzle.py",
    "chia/wallet/nft_wallet/transfer_program_puzzle.py",
    "chia/wallet/did_wallet/did_wallet.py",
    "chia/wallet/did_wallet/did_wallet_puzzles.py",
    "chia/wallet/did_wallet/did_info.py",
    "chia/wallet/wallet_nft_store.py",
    "chia/wallet/wallet_singleton_store.py",

    # =================================================================================
    # Offers, trade settlement, clawback and custody restrictions
    # =================================================================================
    "chia/wallet/trading/offer.py",
    "chia/wallet/trading/trade_store.py",
    "chia/wallet/trading/trade_status.py",
    "chia/wallet/trade_manager.py",
    "chia/wallet/trade_record.py",
    "chia/wallet/util/puzzle_compression.py",
    "chia/wallet/util/puzzle_decorator.py",
    "chia/wallet/util/puzzle_decorator_type.py",
    "chia/wallet/puzzles/clawback/drivers.py",
    "chia/wallet/puzzles/clawback/metadata.py",
    "chia/wallet/puzzles/clawback/puzzle_decorator.py",
    "chia/wallet/puzzles/custody/custody_architecture.py",
    "chia/wallet/puzzles/custody/member_puzzles.py",
    "chia/wallet/puzzles/custody/restrictions.py",
    "chia/wallet/puzzles/custody/restriction_utilities.py",
    "chia/wallet/puzzles/custody/fixed_create_coin_destinations.clsp",
    "chia/wallet/puzzles/custody/heightlock.clsp",
    "chia/wallet/puzzles/custody/send_message_banned.clsp",
    "chia/wallet/notification_manager.py",
    "chia/wallet/notification_store.py",
    "chia/wallet/util/notifications.py",

    # =================================================================================
    # Wallet state, coin ownership records, key derivation and signing
    # =================================================================================
    "chia/wallet/wallet_state_manager.py",
    "chia/wallet/wallet.py",
    "chia/wallet/wsm_apis.py",
    "chia/wallet/wallet_protocol.py",
    "chia/wallet/wallet_coin_store.py",
    "chia/wallet/wallet_coin_record.py",
    "chia/wallet/wallet_puzzle_store.py",
    "chia/wallet/wallet_transaction_store.py",
    "chia/wallet/wallet_interested_store.py",
    "chia/wallet/wallet_retry_store.py",
    "chia/wallet/wallet_user_store.py",
    "chia/wallet/wallet_info.py",
    "chia/wallet/key_val_store.py",
    "chia/wallet/coin_selection.py",
    "chia/wallet/transaction_sorting.py",
    "chia/wallet/transaction_record.py",
    "chia/wallet/wallet_spend_bundle.py",
    "chia/wallet/wallet_action_scope.py",
    "chia/wallet/derivation_record.py",
    "chia/wallet/derive_keys.py",
    "chia/wallet/estimate_fees.py",
    "chia/wallet/signer_protocol.py",
    "chia/wallet/util/signing.py",
    "chia/wallet/util/blind_signer_tl.py",
    "chia/wallet/util/clvm_streamable.py",
    "chia/wallet/util/tx_config.py",
    "chia/wallet/util/address_type.py",
    "chia/wallet/util/transaction_type.py",
    "chia/wallet/util/wallet_types.py",
    "chia/wallet/util/query_filter.py",
    "chia/types/signing_mode.py",

    # =================================================================================
    # Wallet, daemon, keychain and RPC authorization boundaries
    # =================================================================================
    "chia/wallet/wallet_rpc_api.py",
    "chia/wallet/wallet_request_types.py",
    "chia/wallet/remote_wallet/remote_wallet.py",
    "chia/wallet/remote_wallet/remote_coin_store.py",
    "chia/wallet/remote_wallet/remote_info.py",
    "chia/rpc/rpc_server.py",
    "chia/rpc/util.py",
    "chia/rpc/rpc_errors.py",
    "chia/daemon/server.py",
    "chia/daemon/keychain_server.py",
    "chia/daemon/keychain_proxy.py",
    "chia/util/keychain.py",
    "chia/util/file_keyring.py",
    "chia/util/keyring_wrapper.py",
    "chia/util/ws_message.py",

    # =================================================================================
    # Pool wallet, plotnft singleton state machine and reward targets
    # =================================================================================
    "chia/pools/pool_wallet.py",
    "chia/pools/pool_puzzles.py",
    "chia/pools/pool_wallet_info.py",
    "chia/pools/plotnft_drivers.py",
    "chia/pools/pool_config.py",
    "chia/pools/claim_pool_rewards_dpuz.clsp",
    "chia/pools/forward_to_pool_puzzle_hash_dpuz.clsp",
    "chia/wallet/plotnft_wallet/plotnft_wallet.py",
    "chia/wallet/plotnft_wallet/plotnft_store.py",
    "chia/wallet/wallet_pool_store.py",
    "chia/protocols/pool_protocol.py",

    # =================================================================================
    # Data Layer stores, roots, proofs and DL-backed coins
    # =================================================================================
    "chia/data_layer/data_layer.py",
    "chia/data_layer/data_store.py",
    "chia/data_layer/data_layer_wallet.py",
    "chia/data_layer/data_layer_util.py",
    "chia/data_layer/data_layer_rpc_api.py",
    "chia/data_layer/data_layer_rpc_util.py",
    "chia/data_layer/data_layer_server.py",
    "chia/data_layer/data_layer_api.py",
    "chia/data_layer/data_layer_errors.py",
    "chia/data_layer/download_data.py",
    "chia/data_layer/dl_wallet_store.py",
    "chia/data_layer/singleton_record.py",
    "chia/data_layer/util/plugin.py",
    "chia/data_layer/s3_plugin_service.py",
    "chia/wallet/db_wallet/db_wallet_puzzles.py",

    # =================================================================================
    # Serialization, encoding and storage primitives used by validation paths
    # =================================================================================
    "chia/util/streamable.py",
    "chia/util/byte_types.py",
    "chia/util/bech32m.py",
    "chia/util/hash.py",
    "chia/util/casts.py",
    "chia/util/math.py",
    "chia/util/significant_bits.py",
    "chia/util/errors.py",
    "chia/util/db_wrapper.py",
    "chia/util/action_scope.py",
    "chia/util/paginator.py",
    "chia/util/json_util.py",
    "chia/util/batches.py",
    "chia/util/collection.py",
    "chia/util/lru_cache.py",
    "chia/util/recursive_replace.py",
]


target_scopes = [
    "Critical. An unprivileged coin owner or counterparty spends XCH that another key controls, because AGG_SIG_ME/AGG_SIG_UNSAFE message construction, delegated-puzzle or hidden-puzzle handling in p2_delegated_puzzle_or_hidden_puzzle, curry/treehash derivation in curry_and_treehash, or condition parsing in condition_tools lets a spend bundle satisfy a puzzle without the owner's signature, giving direct theft of funds.",
    "Critical. A CAT or CR-CAT holder mints or melts value outside TAIL authorization, because cat_utils ring accounting, extra-delta and lineage-proof handling in cat_outer_puzzle and cr_cat_drivers, TAIL selection in tails.py, or r_cat revocation logic accepts a forged parent, letting an attacker inflate a CAT supply or convert someone else's asset to a different asset.",
    "Critical. An attacker-created coin is accepted as a genuine singleton, because launcher-id derivation, odd-coin selection, or lineage-proof verification in singleton_top_layer, singleton_top_layer_v1_1, chia/wallet/singleton.py, uncurry_nft, or did_wallet_puzzles does not bind the coin to the real launcher, letting the attacker take ownership of an NFT, DID, VC, plotnft, or Data Layer singleton.",
    "Critical. An offer counterparty receives the maker's assets without paying, because offer construction, settlement-payment aggregation, announcement or message pairing, driver-based asset identification in outer_puzzles, or offer compression/decompression in puzzle_compression lets the taker substitute, drop, or reuse a payment while trade_manager still settles the trade as complete.",
    "Critical. A spend bundle that any user can submit is admitted or costed wrongly, because CLVM cost accounting, condition limits, dedup and fast-forward handling in eligible_coin_spends, replace-by-fee rules, or the recheck path in mempool_manager disagrees with block_body_validation, letting an invalid spend reach a block, a valid spend be evicted, or a spend execute without paying its stated fee.",
    "Critical. A single submitted spend bundle makes honest full nodes reach different coin-set state for the same transaction block, because generator serialization in bundle_tools, block reference resolution in get_block_generator, additions/removals derivation, reserve-fee or double-spend checks in block_body_validation, or coin_store persistence depends on ordering, caching, or hard-fork gating, producing a chain split or an invalid block being accepted as valid.",
    "Critical. A spend bundle or coin an unprivileged user submits halts transaction processing on every honest node, because an unhandled exception, arithmetic overflow, or failed assertion in mempool admission, generator or streamable decoding, block body validation, or coin/hint store persistence leaves the node unable to advance the peak, requiring operator intervention.",
    "High. A clawback, custody, or verifiable-credential authorization is bypassed, because timelock and recipient checks in clawback drivers, the puzzle decorator path, restriction and member puzzles in custody, or VC proof-provider and revocation handling in vc_drivers allow the wrong party to claim, recover, or keep a coin whose authority was supposed to have moved or expired.",
    "High. A plotnft or pool participant redirects farming rewards or breaks singleton state, because pool_puzzles target-puzzle-hash and relative-lock-height handling, pool_wallet state transitions between self-pooling, escaping, and farming-to-pool, plotnft_drivers, or wallet_pool_store persistence accepts a transition or payout target the singleton owner never authorized.",
    "High. A Data Layer client or a DL offer counterparty accepts forged store state, because merkle proof verification, root history and singleton record tracking in data_layer_wallet, the mirror and download paths, or data_store node handling binds a root or proof to a coin the attacker created, letting them prove a key/value that was never committed or make honest clients persist the wrong root.",
    "Critical/High blind spot. An unprivileged spend-bundle submitter, wallet user, offer counterparty, pool participant, Data Layer client, or local RPC caller abuses an assumption the protocol never wrote down: a value validated during mempool admission and trusted as already-validated at block time, a puzzle or coin re-derived after the check that authorized it, a condition or limit enforced on one path but not its cached, batched, or aggregated twin, state carried across spend, bundle, block, fork, or wallet-resync boundaries that was only proven safe within one of them, or an error path that persists partial state - yielding unsigned coin movement, forged asset identity, or a node that cannot process valid spends.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one chia-blockchain target.

    ```
    target_file format:
    "'File Name: chia/full_node/mempool_manager.py -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact chia-blockchain target:

    {target_file}

    Project focus:
    chia-blockchain is the Chia full node and wallet. Focus only on what an ordinary user reaches by signing and submitting their own spend bundle, or by acting as a counterparty in a wallet-level flow: mempool admission and CLVM cost, condition parsing and AGG_SIG message construction, coin identity and puzzle hashing, transaction block body validation and coin store state, standard puzzles and singleton lineage, CAT/CR-CAT/VC value flow, NFT/DID ownership, offers and trade settlement, clawback and custody restrictions, wallet state and key derivation, daemon/keychain/RPC authorization, plotnft and pool singleton transitions, and Data Layer roots and proofs.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Python or CLVM symbols (function, method, class, field, puzzle mod) when possible.
    * Attacker is unprivileged only: any key holder who can fund a coin, sign and submit a spend bundle, mint their own CAT/NFT/DID/VC, publish or take an offer, join a pool, run a Data Layer client, or call RPC on their own node. They sign only for their own keys.
    * Attacker is NOT a farmer, timelord, node operator, host or DB owner, pool operator, or holder of another user's key. Never assume a malicious peer, malicious node, malicious farmer, gossip/sync/weight-proof/network attacker, leaked key, compromised host, misconfiguration, or social engineering.
    * Out of scope, never ask about: peer protocol message flooding, node discovery, seeder/introducer, timelord, harvester/plot sync, proof-of-space or VDF cryptography, SSL/cert setup, CLI ergonomics, dependencies.
    * Ignore test files, mocks, simulators, benchmarks, docs, generated code, and TOML/config-only findings.
    * Every question must describe a real spend bundle or wallet action an attacker actually performs. No generic unbounded-allocation, memory-growth, cache-size, or resource-exhaustion speculation; no "what if the input is huge" questions without a concrete submitted spend and a concrete broken invariant.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target unsigned or unauthorized coin movement, asset supply inflation or forged asset identity, offer settlement theft, coin-set divergence between honest nodes, invalid spend or block acceptance, reward redirection, or a submitted spend that stops nodes processing transactions.
    * Every question must be testable by a Python unit test, a CLVM puzzle test, a wallet or full-node simulator test, or a mempool/block-validation test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization is exact: a coin is spent only when the required signature over the exact AGG_SIG message exists, and no driver, decorator, or wrapper grants authority the puzzle did not.
    * Value is conserved: mojo and CAT amounts in equal amounts out plus fees; NFT, DID, VC, and pool singletons stay unique and bound to their real launcher.
    * Determinism holds: every honest node validating the same spend bundle and transaction block reaches the same coin set, cost, and fee result, regardless of ordering, caching, or dedup.
    * Settlement is atomic: an offer, trade, clawback, or pool transition either completes as both parties authorized or leaves no party short.
    * Admission is honest: mempool cost, condition limits, and replacement rules match what block validation will enforce.
    * Execution is total: no attacker-supplied spend bundle, coin, or wallet-level payload can leave a node or wallet unable to process valid spends.

    Each question must include:
    1. target function/method;
    2. attacker action (a concrete spend bundle or wallet action: coins, puzzles, solutions, conditions, signatures);
    3. preconditions (coins and assets the attacker owns and funds);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: unit/CLVM/simulator test PARAMETERS and assert AUTHORIZATION_EXACTNESS, VALUE_CONSERVATION, DETERMINISM, ATOMIC_SETTLEMENT, HONEST_ADMISSION, or TOTAL_EXECUTION.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused chia-blockchain exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: any key holder who funds a coin, signs and submits a spend bundle, mints their own CAT/NFT/DID/VC, publishes or takes an offer, joins a pool, runs a Data Layer client, or calls RPC on their own node. No farmer, timelord, operator, host, DB, pool-operator, or foreign-key access.
- Reject malicious-peer, malicious-node, malicious-farmer, gossip/sync/weight-proof/network-layer, leaked-key, host-level, and misconfiguration-only paths.
- Reject seeder/introducer, timelord, harvester/plot-sync, proof-of-space and VDF cryptography, SSL setup, CLI, metrics, dependency-only, and test/mock/simulator/docs/generated/config-only findings.
- Reject generic unbounded-allocation or resource-growth claims with no concrete spend bundle and no broken invariant.
- This program pays High and Critical only. Focus on real chain impact: unsigned or unauthorized coin movement, CAT supply inflation or forged asset identity, offer settlement theft, coin-set divergence between honest nodes, invalid spend or block acceptance, reward redirection, or a submitted spend that stops nodes processing transactions.

## Validate
- Trace the exact reachable path from the attacker's spend bundle or wallet action (coins, puzzles, solutions, conditions, signatures) into the affected function.
- Check whether signature verification, condition limits, CLVM cost checks, lineage and puzzle-hash binding, ownership checks, or existing error handling already stop it.
- Confirm the path is reachable on current mainnet consensus constants and the active hard-fork rules.
- Accept only concrete unsigned coin movement, forged asset or singleton identity, settlement theft, state divergence, invalid block acceptance, reward redirection, or a transaction-processing halt.
- Require exact file/function support and a reproducible Python unit, CLVM puzzle, mempool/block-validation, or simulator PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker spend bundle inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (loss of funds, forged asset, consensus divergence, invalid block acceptance) or High (authorization bypass, state corruption, long-lived inability to process valid spends)]

### Likelihood Explanation
[Preconditions, coins and assets needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Python unit/CLVM/simulator test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for chia-blockchain.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs an unprivileged spend-bundle submitter, wallet user, offer counterparty, pool participant, Data Layer client, or local RPC caller can reach: mempool admission and CLVM cost, condition and AGG_SIG handling, coin identity and puzzle hashing, block body validation and coin store state, standard puzzles and singleton lineage, CAT/CR-CAT/VC flow, NFT/DID ownership, offers and trades, clawback and custody, wallet state and key derivation, daemon/keychain/RPC authorization, plotnft and pool transitions, or Data Layer roots and proofs.
- Reject malicious-peer, malicious-node, malicious-farmer, network-layer, leaked-key, operator-only, timelord, harvester/plot-sync, proof-of-space/VDF cryptography, SSL, CLI, mocked-only paths, dependency-only bugs, and no-impact analogs.
- Medium , High and Critical only; no low, or resource-only analogs.

## Validate
- Map the bug class to the strongest reachable chia path from a single submitted spend bundle or wallet action.
- Prove root cause with exact file/function support.
- Accept only concrete unsigned or unauthorized coin movement, supply inflation or forged asset identity, offer settlement theft, coin-set divergence between honest nodes, invalid spend or block acceptance, reward redirection, or a spend-triggered transaction-processing halt.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for chia-blockchain security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- This program pays High and Critical only; reject low, medium, informational, best-practice, and resource-only reports.
- Reject malicious-peer, malicious-node, malicious-farmer, network-layer, seeder/introducer, timelord, harvester/plot-sync, proof-of-space and VDF cryptography, SSL/cert, CLI, metrics, dependency-only, docs/style, generated-file, and test/mock/simulator/config-only issues.
- Reject if the exploit needs farmer, timelord, operator, host, database, or pool-operator access, another user's key, victim social engineering, a non-default configuration, or anything outside what an unprivileged key holder can put in a spend bundle or a wallet-level action.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged spend-bundle submitter, wallet user, offer counterparty, pool participant, Data Layer client, or local RPC caller, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope category: Critical - unsigned or unauthorized movement of XCH/CAT/NFT/DID/VC/pool/Data Layer coins, CAT supply inflation or forged singleton identity, offer settlement theft, coin-set divergence between honest nodes, or invalid spend/block acceptance; High - wallet, daemon, keychain, RPC, pool, or Data Layer authorization bypass, corruption of coin/lineage/trade/pool/Data Layer state, reward redirection, or long-lived inability of honest nodes and wallets to process valid spends.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken authorization, value-conservation, determinism, settlement, admission, or total-execution invariant.
3. Reachable exploit path: preconditions (attacker-owned coins and assets) -> submitted spend bundle or wallet action -> trigger -> bad result.
4. Existing signature checks, condition limits, CLVM cost checks, lineage and puzzle-hash binding, ownership checks, and error handling reviewed and shown insufficient.
5. Concrete in-scope High/Critical impact with realistic likelihood.
6. Reproducible proof path: Python unit PoC, CLVM puzzle test, mempool/block-validation test, wallet or full-node simulator test, or exact steps against a local network.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary key holder trigger this with a spend bundle or wallet action, without farmer, operator, host, or foreign-key access?
- Does the code actually behave as claimed under current mainnet consensus constants and active hard-fork rules?
- Is the impact caused by this code, not by a malicious peer, farmer, plugin, or dependency?
- Is the theft, forged identity, divergence, or halt concrete rather than hypothetical?
- Would a Chia Network triager accept the proof-of-concept?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and Chia bounty category]

## Likelihood Explanation
[Attacker capability, coins and assets required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Python unit/CLVM/simulator test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
