import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'stacks-network/stacks-core'
# todo: the name of the repository
REPO_NAME = 'stacks-core'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


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
    # LENS: TRANSACTION AUTHENTICATION AND POST-CONDITION ENFORCEMENT.
    # Every Stacks transaction is bytes an unprivileged sender chose. The files below sit
    # on the path from those bytes - the auth structure, nonce, fee, chain id, version,
    # payload and post-conditions - to one of three decisions: is the signer who they
    # claim and did they authorise exactly this transaction, does every asset the
    # transaction moves satisfy a post-condition, and does the fee/nonce debited equal
    # what the sender committed. A question belongs here only if it can be closed by an
    # equality between what was authenticated and what was executed or charged.
    # =================================================================================
    # -- clarity-types: Clarity value, type and effect model -------------------------------
    "clarity-types/src/effects/asset_map.rs",
    "clarity-types/src/effects/mod.rs",
    "clarity-types/src/errors/mod.rs",
    "clarity-types/src/lib.rs",
    "clarity-types/src/representations.rs",
    "clarity-types/src/types/mod.rs",
    "clarity-types/src/types/serialization.rs",
    "clarity-types/src/types/signatures.rs",
    "clarity-types/src/version.rs",

    # -- clarity: the Clarity language, analyser, interpreter, costs and database ----------
    "clarity/src/libclarity.rs",
    "clarity/src/vm/analysis/analysis_db.rs",
    "clarity/src/vm/analysis/arithmetic_checker/mod.rs",
    "clarity/src/vm/analysis/contract_interface_builder/mod.rs",
    "clarity/src/vm/analysis/errors.rs",
    "clarity/src/vm/analysis/mod.rs",
    "clarity/src/vm/analysis/read_only_checker/mod.rs",
    "clarity/src/vm/analysis/trait_checker/mod.rs",
    "clarity/src/vm/analysis/type_checker/contexts.rs",
    "clarity/src/vm/analysis/type_checker/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/contexts.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/assets.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/maps.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/options.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/sequences.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/contexts.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/assets.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/conversions.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/maps.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/options.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/post_conditions.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/sequences.rs",
    "clarity/src/vm/analysis/types.rs",
    "clarity/src/vm/ast/definition_sorter/mod.rs",
    "clarity/src/vm/ast/errors.rs",
    "clarity/src/vm/ast/expression_identifier/mod.rs",
    "clarity/src/vm/ast/mod.rs",
    "clarity/src/vm/ast/parser/mod.rs",
    "clarity/src/vm/ast/parser/v1.rs",
    "clarity/src/vm/ast/parser/v2/lexer/error.rs",
    "clarity/src/vm/ast/parser/v2/lexer/mod.rs",
    "clarity/src/vm/ast/parser/v2/lexer/token.rs",
    "clarity/src/vm/ast/parser/v2/mod.rs",
    "clarity/src/vm/ast/stack_depth_checker.rs",
    "clarity/src/vm/ast/sugar_expander/mod.rs",
    "clarity/src/vm/ast/traits_resolver/mod.rs",
    "clarity/src/vm/ast/types.rs",
    "clarity/src/vm/callables.rs",
    "clarity/src/vm/clarity.rs",
    "clarity/src/vm/contexts.rs",
    "clarity/src/vm/contracts.rs",
    "clarity/src/vm/costs/constants.rs",
    "clarity/src/vm/costs/cost_functions.rs",
    "clarity/src/vm/costs/costs_1.rs",
    "clarity/src/vm/costs/costs_2.rs",
    "clarity/src/vm/costs/costs_2_testnet.rs",
    "clarity/src/vm/costs/costs_3.rs",
    "clarity/src/vm/costs/costs_4.rs",
    "clarity/src/vm/costs/costs_5.rs",
    "clarity/src/vm/costs/errors.rs",
    "clarity/src/vm/costs/execution_cost.rs",
    "clarity/src/vm/costs/mod.rs",
    "clarity/src/vm/database/caching/mod.rs",
    "clarity/src/vm/database/caching/weight_limited_fifo.rs",
    "clarity/src/vm/database/clarity_db.rs",
    "clarity/src/vm/database/clarity_store.rs",
    "clarity/src/vm/database/key_value_wrapper.rs",
    "clarity/src/vm/database/mod.rs",
    "clarity/src/vm/database/sqlite.rs",
    "clarity/src/vm/database/structures.rs",
    "clarity/src/vm/diagnostic.rs",
    "clarity/src/vm/errors.rs",
    "clarity/src/vm/events.rs",
    "clarity/src/vm/functions/arithmetic.rs",
    "clarity/src/vm/functions/assets.rs",
    "clarity/src/vm/functions/bitcoin.rs",
    "clarity/src/vm/functions/boolean.rs",
    "clarity/src/vm/functions/conversions.rs",
    "clarity/src/vm/functions/crypto.rs",
    "clarity/src/vm/functions/database.rs",
    "clarity/src/vm/functions/define.rs",
    "clarity/src/vm/functions/mod.rs",
    "clarity/src/vm/functions/options.rs",
    "clarity/src/vm/functions/post_conditions.rs",
    "clarity/src/vm/functions/principals.rs",
    "clarity/src/vm/functions/sequences.rs",
    "clarity/src/vm/functions/tuples.rs",
    "clarity/src/vm/hooks/internals.rs",
    "clarity/src/vm/hooks/mod.rs",
    "clarity/src/vm/hooks/trace.rs",
    "clarity/src/vm/mod.rs",
    "clarity/src/vm/representations.rs",
    "clarity/src/vm/resource_limiter.rs",
    "clarity/src/vm/tooling/mod.rs",
    "clarity/src/vm/types/mod.rs",
    "clarity/src/vm/types/serialization.rs",
    "clarity/src/vm/types/signatures.rs",
    "clarity/src/vm/variables.rs",
    "clarity/src/vm/version.rs",

    # -- stacks-codec: transaction and message wire encoding -------------------------------
    "stacks-codec/src/lib.rs",
    "stacks-codec/src/strings.rs",
    "stacks-codec/src/transaction.rs",

    # -- crates/stacks-transactions: standalone transaction and post-condition checks ------
    "crates/stacks-transactions/src/lib.rs",

    # -- stacks-common: addresses, hashing, secp256k1, codec and shared utils --------------
    "stacks-common/src/address/b58.rs",
    "stacks-common/src/address/c32.rs",
    "stacks-common/src/address/c32_old.rs",
    "stacks-common/src/address/mod.rs",
    "stacks-common/src/alloc_tracker.rs",
    "stacks-common/src/bitvec.rs",
    "stacks-common/src/codec/macros.rs",
    "stacks-common/src/codec/mod.rs",
    "stacks-common/src/libcommon.rs",
    "stacks-common/src/types/chainstate.rs",
    "stacks-common/src/types/mod.rs",
    "stacks-common/src/types/net.rs",
    "stacks-common/src/types/sqlite.rs",
    "stacks-common/src/util/chunked_encoding.rs",
    "stacks-common/src/util/db.rs",
    "stacks-common/src/util/ed25519.rs",
    "stacks-common/src/util/hash.rs",
    "stacks-common/src/util/log.rs",
    "stacks-common/src/util/lru_cache.rs",
    "stacks-common/src/util/macros.rs",
    "stacks-common/src/util/mod.rs",
    "stacks-common/src/util/pair.rs",
    "stacks-common/src/util/pipe.rs",
    "stacks-common/src/util/retry.rs",
    "stacks-common/src/util/secp256k1/mod.rs",
    "stacks-common/src/util/secp256k1/native.rs",
    "stacks-common/src/util/secp256k1/wasm.rs",
    "stacks-common/src/util/secp256r1.rs",
    "stacks-common/src/util/serde_serializers.rs",
    "stacks-common/src/util/uint.rs",
    "stacks-common/src/util/vrf.rs",

    # -- libsigner: signer transport, events and v0 messages -------------------------------
    "libsigner/src/error.rs",
    "libsigner/src/events.rs",
    "libsigner/src/http.rs",
    "libsigner/src/libsigner.rs",
    "libsigner/src/runloop.rs",
    "libsigner/src/session.rs",
    "libsigner/src/signer_set.rs",
    "libsigner/src/v0/messages.rs",
    "libsigner/src/v0/mod.rs",
    "libsigner/src/v0/signer_state.rs",

    # -- libstackerdb: StackerDB chunk signing and verification ----------------------------
    "libstackerdb/src/libstackerdb.rs",

    # -- pox-locking: the Rust side that locks and unlocks STX for PoX/stacking ------------
    "pox-locking/src/events.rs",
    "pox-locking/src/events_24.rs",
    "pox-locking/src/lib.rs",
    "pox-locking/src/pox_1.rs",
    "pox-locking/src/pox_2.rs",
    "pox-locking/src/pox_3.rs",
    "pox-locking/src/pox_4.rs",
    "pox-locking/src/pox_5.rs",

    # -- stacks-signer: the Nakamoto signer decision logic and chainstate view -------------
    "stacks-signer/src/chainstate/mod.rs",
    "stacks-signer/src/chainstate/v1.rs",
    "stacks-signer/src/chainstate/v2.rs",
    "stacks-signer/src/cli.rs",
    "stacks-signer/src/client/mod.rs",
    "stacks-signer/src/client/stackerdb.rs",
    "stacks-signer/src/client/stacks_client.rs",
    "stacks-signer/src/config.rs",
    "stacks-signer/src/lib.rs",
    "stacks-signer/src/main.rs",
    "stacks-signer/src/monitor_signers.rs",
    "stacks-signer/src/monitoring/mod.rs",
    "stacks-signer/src/monitoring/prometheus.rs",
    "stacks-signer/src/monitoring/server.rs",
    "stacks-signer/src/runloop.rs",
    "stacks-signer/src/signerdb.rs",
    "stacks-signer/src/utils.rs",
    "stacks-signer/src/v0/mod.rs",
    "stacks-signer/src/v0/signer.rs",
    "stacks-signer/src/v0/signer_state.rs",

    # -- stacks-node: the node binary, run loops, miner, burnchain and event dispatch ------
    "stacks-node/src/burnchains/bitcoin/core_controller.rs",
    "stacks-node/src/burnchains/bitcoin/mod.rs",
    "stacks-node/src/burnchains/bitcoin_regtest_controller.rs",
    "stacks-node/src/burnchains/mod.rs",
    "stacks-node/src/burnchains/rpc/bitcoin_rpc_client/mod.rs",
    "stacks-node/src/burnchains/rpc/mod.rs",
    "stacks-node/src/burnchains/rpc/rpc_transport/mod.rs",
    "stacks-node/src/event_dispatcher.rs",
    "stacks-node/src/event_dispatcher/db.rs",
    "stacks-node/src/event_dispatcher/payloads.rs",
    "stacks-node/src/event_dispatcher/stacker_db.rs",
    "stacks-node/src/event_dispatcher/worker.rs",
    "stacks-node/src/globals.rs",
    "stacks-node/src/keychain.rs",
    "stacks-node/src/main.rs",
    "stacks-node/src/monitoring/mod.rs",
    "stacks-node/src/monitoring/prometheus.rs",
    "stacks-node/src/nakamoto_node.rs",
    "stacks-node/src/nakamoto_node/miner.rs",
    "stacks-node/src/nakamoto_node/miner_db.rs",
    "stacks-node/src/nakamoto_node/peer.rs",
    "stacks-node/src/nakamoto_node/relayer.rs",
    "stacks-node/src/nakamoto_node/signer_coordinator.rs",
    "stacks-node/src/nakamoto_node/stackerdb_listener.rs",
    "stacks-node/src/neon_node.rs",
    "stacks-node/src/node.rs",
    "stacks-node/src/operations.rs",
    "stacks-node/src/run_loop/boot_nakamoto.rs",
    "stacks-node/src/run_loop/helium.rs",
    "stacks-node/src/run_loop/mod.rs",
    "stacks-node/src/run_loop/nakamoto.rs",
    "stacks-node/src/run_loop/neon.rs",
    "stacks-node/src/syncctl.rs",
    "stacks-node/src/tenure.rs",

    # -- stackslib: consensus, chainstate, the Clarity VM host, burn ops and the P2P/RPC network ----
    "stackslib/src/burnchains/bitcoin/address.rs",
    "stackslib/src/burnchains/bitcoin/bits.rs",
    "stackslib/src/burnchains/bitcoin/blocks.rs",
    "stackslib/src/burnchains/bitcoin/indexer.rs",
    "stackslib/src/burnchains/bitcoin/keys.rs",
    "stackslib/src/burnchains/bitcoin/messages.rs",
    "stackslib/src/burnchains/bitcoin/mod.rs",
    "stackslib/src/burnchains/bitcoin/network.rs",
    "stackslib/src/burnchains/bitcoin/spv.rs",
    "stackslib/src/burnchains/burnchain.rs",
    "stackslib/src/burnchains/db.rs",
    "stackslib/src/burnchains/indexer.rs",
    "stackslib/src/burnchains/mod.rs",
    "stackslib/src/chainstate/burn/atc.rs",
    "stackslib/src/chainstate/burn/db/mod.rs",
    "stackslib/src/chainstate/burn/db/processing.rs",
    "stackslib/src/chainstate/burn/db/sortdb.rs",
    "stackslib/src/chainstate/burn/distribution.rs",
    "stackslib/src/chainstate/burn/mod.rs",
    "stackslib/src/chainstate/burn/operations/delegate_stx.rs",
    "stackslib/src/chainstate/burn/operations/leader_block_commit.rs",
    "stackslib/src/chainstate/burn/operations/leader_key_register.rs",
    "stackslib/src/chainstate/burn/operations/mod.rs",
    "stackslib/src/chainstate/burn/operations/stack_stx.rs",
    "stackslib/src/chainstate/burn/operations/transfer_stx.rs",
    "stackslib/src/chainstate/burn/operations/vote_for_aggregate_key.rs",
    "stackslib/src/chainstate/burn/sortition.rs",
    "stackslib/src/chainstate/coordinator/comm.rs",
    "stackslib/src/chainstate/coordinator/mod.rs",
    "stackslib/src/chainstate/mod.rs",
    "stackslib/src/chainstate/nakamoto/coordinator/mod.rs",
    "stackslib/src/chainstate/nakamoto/keys.rs",
    "stackslib/src/chainstate/nakamoto/miner.rs",
    "stackslib/src/chainstate/nakamoto/mod.rs",
    "stackslib/src/chainstate/nakamoto/shadow.rs",
    "stackslib/src/chainstate/nakamoto/signer_set.rs",
    "stackslib/src/chainstate/nakamoto/staging_blocks.rs",
    "stackslib/src/chainstate/nakamoto/tenure.rs",
    "stackslib/src/chainstate/stacks/address.rs",
    "stackslib/src/chainstate/stacks/auth.rs",
    "stackslib/src/chainstate/stacks/block.rs",
    "stackslib/src/chainstate/stacks/boot/bns.clar",
    "stackslib/src/chainstate/stacks/boot/contract_tests.rs",
    "stackslib/src/chainstate/stacks/boot/cost-voting.clar",
    "stackslib/src/chainstate/stacks/boot/costs-2.clar",
    "stackslib/src/chainstate/stacks/boot/costs-3.clar",
    "stackslib/src/chainstate/stacks/boot/costs-4.clar",
    "stackslib/src/chainstate/stacks/boot/costs.clar",
    "stackslib/src/chainstate/stacks/boot/docs.rs",
    "stackslib/src/chainstate/stacks/boot/genesis.clar",
    "stackslib/src/chainstate/stacks/boot/lockup.clar",
    "stackslib/src/chainstate/stacks/boot/mod.rs",
    "stackslib/src/chainstate/stacks/boot/pox-2.clar",
    "stackslib/src/chainstate/stacks/boot/pox-3.clar",
    "stackslib/src/chainstate/stacks/boot/pox-4.clar",
    "stackslib/src/chainstate/stacks/boot/pox-5.clar",
    "stackslib/src/chainstate/stacks/boot/pox-mainnet.clar",
    "stackslib/src/chainstate/stacks/boot/pox.clar",
    "stackslib/src/chainstate/stacks/boot/pox_2_tests.rs",
    "stackslib/src/chainstate/stacks/boot/pox_3_tests.rs",
    "stackslib/src/chainstate/stacks/boot/pox_4_tests.rs",
    "stackslib/src/chainstate/stacks/boot/signers-0-xxx.clar",
    "stackslib/src/chainstate/stacks/boot/signers-1-xxx.clar",
    "stackslib/src/chainstate/stacks/boot/signers-voting.clar",
    "stackslib/src/chainstate/stacks/boot/signers.clar",
    "stackslib/src/chainstate/stacks/boot/signers_tests.rs",
    "stackslib/src/chainstate/stacks/boot/sip-031.clar",
    "stackslib/src/chainstate/stacks/db/accounts.rs",
    "stackslib/src/chainstate/stacks/db/blocks.rs",
    "stackslib/src/chainstate/stacks/db/contracts.rs",
    "stackslib/src/chainstate/stacks/db/headers.rs",
    "stackslib/src/chainstate/stacks/db/mod.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/blocks.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/burnchain.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/clarity.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/common.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/fork_storage.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/index.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/mod.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/sortition.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/spv.rs",
    "stackslib/src/chainstate/stacks/db/transactions.rs",
    "stackslib/src/chainstate/stacks/db/unconfirmed.rs",
    "stackslib/src/chainstate/stacks/events.rs",
    "stackslib/src/chainstate/stacks/index/bits.rs",
    "stackslib/src/chainstate/stacks/index/blob_layout.rs",
    "stackslib/src/chainstate/stacks/index/cache.rs",
    "stackslib/src/chainstate/stacks/index/file.rs",
    "stackslib/src/chainstate/stacks/index/marf.rs",
    "stackslib/src/chainstate/stacks/index/mod.rs",
    "stackslib/src/chainstate/stacks/index/node.rs",
    "stackslib/src/chainstate/stacks/index/profile.rs",
    "stackslib/src/chainstate/stacks/index/proofs.rs",
    "stackslib/src/chainstate/stacks/index/squash.rs",
    "stackslib/src/chainstate/stacks/index/squash/node_store.rs",
    "stackslib/src/chainstate/stacks/index/squash/stream.rs",
    "stackslib/src/chainstate/stacks/index/storage.rs",
    "stackslib/src/chainstate/stacks/index/trie.rs",
    "stackslib/src/chainstate/stacks/index/trie_sql.rs",
    "stackslib/src/chainstate/stacks/miner.rs",
    "stackslib/src/chainstate/stacks/mod.rs",
    "stackslib/src/chainstate/stacks/sbtc.rs",
    "stackslib/src/chainstate/stacks/transaction.rs",
    "stackslib/src/clarity_vm/clarity.rs",
    "stackslib/src/clarity_vm/database/ephemeral.rs",
    "stackslib/src/clarity_vm/database/marf.rs",
    "stackslib/src/clarity_vm/database/mod.rs",
    "stackslib/src/clarity_vm/mod.rs",
    "stackslib/src/clarity_vm/special.rs",
    "stackslib/src/config/chain_data.rs",
    "stackslib/src/config/mod.rs",
    "stackslib/src/core/mempool.rs",
    "stackslib/src/core/mod.rs",
    "stackslib/src/core/nonce_cache.rs",
    "stackslib/src/cost_estimates/fee_medians.rs",
    "stackslib/src/cost_estimates/fee_rate_fuzzer.rs",
    "stackslib/src/cost_estimates/fee_scalar.rs",
    "stackslib/src/cost_estimates/metrics.rs",
    "stackslib/src/cost_estimates/mod.rs",
    "stackslib/src/cost_estimates/pessimistic.rs",
    "stackslib/src/deps/mod.rs",
    "stackslib/src/lib.rs",
    "stackslib/src/monitoring/mod.rs",
    "stackslib/src/monitoring/prometheus.rs",
    "stackslib/src/net/api/blockreplay.rs",
    "stackslib/src/net/api/blocksimulate.rs",
    "stackslib/src/net/api/callreadonly.rs",
    "stackslib/src/net/api/fastcallreadonly.rs",
    "stackslib/src/net/api/get_tenure_tip_meta.rs",
    "stackslib/src/net/api/get_tenures_fork_info.rs",
    "stackslib/src/net/api/getaccount.rs",
    "stackslib/src/net/api/getattachment.rs",
    "stackslib/src/net/api/getattachmentsinv.rs",
    "stackslib/src/net/api/getblock.rs",
    "stackslib/src/net/api/getblock_v3.rs",
    "stackslib/src/net/api/getblockbyheight.rs",
    "stackslib/src/net/api/getclaritymarfvalue.rs",
    "stackslib/src/net/api/getclaritymetadata.rs",
    "stackslib/src/net/api/getconstantval.rs",
    "stackslib/src/net/api/getcontractabi.rs",
    "stackslib/src/net/api/getcontractsrc.rs",
    "stackslib/src/net/api/getdatavar.rs",
    "stackslib/src/net/api/getheaders.rs",
    "stackslib/src/net/api/gethealth.rs",
    "stackslib/src/net/api/getinfo.rs",
    "stackslib/src/net/api/getistraitimplemented.rs",
    "stackslib/src/net/api/getmapentry.rs",
    "stackslib/src/net/api/getmicroblocks_confirmed.rs",
    "stackslib/src/net/api/getmicroblocks_indexed.rs",
    "stackslib/src/net/api/getmicroblocks_unconfirmed.rs",
    "stackslib/src/net/api/getneighbors.rs",
    "stackslib/src/net/api/getpoxinfo.rs",
    "stackslib/src/net/api/getsigner.rs",
    "stackslib/src/net/api/getsortition.rs",
    "stackslib/src/net/api/getstackerdbchunk.rs",
    "stackslib/src/net/api/getstackerdbmetadata.rs",
    "stackslib/src/net/api/getstackers.rs",
    "stackslib/src/net/api/getstxtransfercost.rs",
    "stackslib/src/net/api/gettenure.rs",
    "stackslib/src/net/api/gettenureblocks.rs",
    "stackslib/src/net/api/gettenureblocksbyhash.rs",
    "stackslib/src/net/api/gettenureblocksbyheight.rs",
    "stackslib/src/net/api/gettenureinfo.rs",
    "stackslib/src/net/api/gettenuretip.rs",
    "stackslib/src/net/api/gettransaction.rs",
    "stackslib/src/net/api/gettransaction_unconfirmed.rs",
    "stackslib/src/net/api/liststackerdbreplicas.rs",
    "stackslib/src/net/api/mod.rs",
    "stackslib/src/net/api/postblock.rs",
    "stackslib/src/net/api/postblock_proposal.rs",
    "stackslib/src/net/api/postblock_v3.rs",
    "stackslib/src/net/api/postfeerate.rs",
    "stackslib/src/net/api/postmempoolquery.rs",
    "stackslib/src/net/api/postmicroblock.rs",
    "stackslib/src/net/api/poststackerdbchunk.rs",
    "stackslib/src/net/api/posttransaction.rs",
    "stackslib/src/net/api/read_only/mod.rs",
    "stackslib/src/net/api/read_only/parse.rs",
    "stackslib/src/net/api/txsimulate.rs",
    "stackslib/src/net/asn.rs",
    "stackslib/src/net/atlas/db.rs",
    "stackslib/src/net/atlas/download.rs",
    "stackslib/src/net/atlas/mod.rs",
    "stackslib/src/net/chat.rs",
    "stackslib/src/net/codec.rs",
    "stackslib/src/net/connection.rs",
    "stackslib/src/net/db.rs",
    "stackslib/src/net/dns.rs",
    "stackslib/src/net/download/epoch2x.rs",
    "stackslib/src/net/download/mod.rs",
    "stackslib/src/net/download/nakamoto/download_state_machine.rs",
    "stackslib/src/net/download/nakamoto/mod.rs",
    "stackslib/src/net/download/nakamoto/tenure.rs",
    "stackslib/src/net/download/nakamoto/tenure_downloader.rs",
    "stackslib/src/net/download/nakamoto/tenure_downloader_set.rs",
    "stackslib/src/net/download/nakamoto/tenure_downloader_unconfirmed.rs",
    "stackslib/src/net/http/common.rs",
    "stackslib/src/net/http/error.rs",
    "stackslib/src/net/http/mod.rs",
    "stackslib/src/net/http/request.rs",
    "stackslib/src/net/http/response.rs",
    "stackslib/src/net/http/stream.rs",
    "stackslib/src/net/httpcore.rs",
    "stackslib/src/net/inv/epoch2x.rs",
    "stackslib/src/net/inv/mod.rs",
    "stackslib/src/net/inv/nakamoto.rs",
    "stackslib/src/net/mempool/mod.rs",
    "stackslib/src/net/mod.rs",
    "stackslib/src/net/neighbors/comms.rs",
    "stackslib/src/net/neighbors/db.rs",
    "stackslib/src/net/neighbors/mod.rs",
    "stackslib/src/net/neighbors/neighbor.rs",
    "stackslib/src/net/neighbors/rpc.rs",
    "stackslib/src/net/neighbors/walk.rs",
    "stackslib/src/net/p2p.rs",
    "stackslib/src/net/poll.rs",
    "stackslib/src/net/prune.rs",
    "stackslib/src/net/relay.rs",
    "stackslib/src/net/rpc.rs",
    "stackslib/src/net/server.rs",
    "stackslib/src/net/stackerdb/config.rs",
    "stackslib/src/net/stackerdb/db.rs",
    "stackslib/src/net/stackerdb/mod.rs",
    "stackslib/src/net/stackerdb/sync.rs",
    "stackslib/src/net/unsolicited.rs",
    "stackslib/src/util_lib/bloom.rs",
    "stackslib/src/util_lib/boot.rs",
    "stackslib/src/util_lib/db.rs",
    "stackslib/src/util_lib/mod.rs",
    "stackslib/src/util_lib/signed_structured_data.rs",
    "stackslib/src/util_lib/strings.rs",

    # =================================================================================
    # NOT AUDITED (excluded from every variant): tests, mocks and *test* files; fuzz and
    # bench harnesses; test_util and the hooks/testing render helpers; docs/ and README;
    # config, *.toml and CHANGELOG; generated tables (stx-genesis, genesis_data.rs) and
    # build.rs; vendored third-party code under deps_common/ (bitcoin, httparse, bech32,
    # ctrlc); the contrib/ tools and stacks-profiler; sample/ example contracts; and the
    # *-testnet / *.tests.clar network- and test-only contract bodies. A defect in any of
    # these is only in scope when it is reachable from the audited code above.
    # =================================================================================
]


target_scopes = [
    "Critical. THE SIGNATURE HASH MUST COVER EVERY FIELD THE NODE ACTS ON. `StacksTransaction::verify` -> `verify_origin` (codec transaction.rs) rebuilds the signing hash via `next_signature` over a cleared-auth transaction, then recovers the pubkey and compares its `Hash160` to the auth's `signer`. Find a field the node acts on that is not bound into that hash, or a re-encoding that produces the same hash for two different transactions: a post-condition list or `post_condition_mode` altered after signing, a `TransactionPayload` field the clearing step blanks and never restores, an `anchor_mode` or `sponsor` toggled, an auth field order in order-independent multisig that changes the executed set but not the digest. Show an unprivileged attacker taking a validly signed transaction and mutating it into a different executed transaction with the same recovered signer. Identity: the transaction the recovered public key authenticated == the transaction the node executes and charges.",

    "Critical. MULTISIG THRESHOLD MUST EQUAL SIGNATURES VERIFIED. `TransactionAuth` singlesig, `MultisigSpendingCondition`, `OrderIndependentMultisigSpendingCondition` and their `verify` / `push_signature` / `push_public_key` / `pop_auth_field` decide when enough distinct keys signed; `next_signature` threads the running hash for sequential multisig. Show an auth that verifies with fewer distinct signers than the required `signatures_required`, or where one signature is counted twice: a duplicated `TransactionAuthField::Signature`, a public-key field that fills a slot without a signature in order-independent mode, a recovered key that matches the address hash but signs a different sighash than the thread expects, a `signatures_required` of zero accepted. Identity: the count of distinct authorized keys whose signatures verify over the correct sighash == the `signatures_required` encoded in the spending condition.",

    "Critical. LOW-S AND RECOVERY MUST NOT ADMIT A SECOND VALID SIGNATURE. `secp256k1/native.rs` `recover_to_pubkey`, `recover_to_pubkey_without_validating_low_s`, `secp256k1_verify` and `MessageSignature` conversions decide signature admissibility; `TransactionAuthVerificationMode` (from `allows_tx_signatures_with_high_s`) selects whether high-S is rejected in the current epoch. Show a transaction where a malleated signature (flipped S, alternate recovery id) recovers the same signer and is accepted, so the same authorized transaction has two distinct txids, or where the epoch gate lets a high-S signature through that a later epoch's mempool or block check rejects. Identity: the set of byte-distinct signatures the node accepts for one (signer, sighash) == exactly the canonical one the epoch's verification mode permits.",

    "Critical. THE SPONSOR PAYS; THE ORIGIN COMMANDS. In a sponsored transaction `is_sponsored`, `verify` (origin then sponsor), `get_origin_nonce`, `get_sponsor_nonce`, `get_tx_fee`, and the account projection in `transactions.rs` split authority: the origin authorises the payload, the sponsor authorises the fee. Show a sponsored transaction where the fee is charged to the wrong account, the origin's payload executes without a valid sponsor signature, the sponsor nonce and origin nonce are checked against the wrong accounts, or a non-sponsored transaction is processed through the sponsored path so `tx-sponsor?` reports an account that never signed. Identity: the account debited the fee == the sponsor who signed the sponsor auth, and the account whose nonce and payload authority are consumed == the origin who signed the origin auth.",

    "Critical. POST-CONDITIONS MUST COVER EVERY ASSET THAT MOVED. `check_transaction_postconditions` (stacks-transactions) compares the transaction's `TransactionPostCondition` list under `TransactionPostConditionMode` against the `AssetMap` the VM produced; `FungibleConditionCode::check`, `AssetInfoID` and the STX/FT/NFT branches decide pass/fail. Show a transfer that escapes its post-conditions: `Allow` mode letting an unexpected asset move (intended), but also `Deny` mode where an asset moved by a `contract-call?` sub-call is not attributed to the sender the post-condition names, an NFT identified by a `Value` that the check compares by a different encoding than the AssetMap stored, an STX post-condition satisfied by burn versus transfer, a memo transfer counted under the wrong code. Identity: every asset movement in the committed AssetMap == an asset movement permitted by the transaction's post-conditions under its mode.",

    "Critical. THE EPOCH GATE MUST REJECT EVERY UNSUPPORTED TRANSACTION IDENTICALLY. `process_transaction_precheck` / `validate_transactions_static_epoch_and_process_transaction` check `tx.auth.is_supported_in_epoch`, `chain_id`, `version`, `check_post_conditions_supported_in_epoch`, and the `SmartContract(_, Some(clarity_version))` bound against `ClarityVersion::default_for_epoch`. Show a transaction accepted in one epoch or by one node's gate but not another's, or a version/chain-id/hash-mode combination that the mempool admits and a block applies inconsistently: an order-independent multisig auth not supported before its epoch, a post-condition mode gated differently in codec versus stacks-transactions, a `chain_id` compared against the wrong network constant, a Clarity version newer than the epoch that a soft check lets through. Identity: the set of transactions node A's epoch gate admits == the set node B admits at the same tip.",

    "Critical. NONCE AND FEE DEBIT MUST EQUAL WHAT THE SENDER COMMITTED. `check_transaction_nonces`, `get_nonce`, `update_account_nonce`, `account_debit`, and the fee charge in `process_transaction` decide the account's next nonce and balance; `nonce_cache.rs` and the mempool mirror it. Show a transaction that executes without advancing the nonce (replayable), advances the nonce twice, is charged a fee different from `get_tx_fee`, or is charged against a balance snapshot taken after a payload that already spent it: a sponsored fee debited before the origin payload reverts, a nonce checked against the cache but committed against the DB, an abort path that keeps the payload's writes but not the fee. Identity: after a transaction, the account's nonce == committed nonce + 1 and its balance == prior balance minus exactly `get_tx_fee` and the payload's authorized spends.",

    "High. THE MEMPOOL GATE MUST MATCH THE BLOCK GATE. `will_admit_mempool_tx` -> `can_include_tx` in db/blocks.rs enforces `process_transaction_precheck`, the `MINIMUM_TX_FEE` / `MINIMUM_TX_FEE_RATE_PER_BYTE` floor against `tx_size`, and epoch, before `posttransaction.rs` relays a tx. Show a transaction the mempool admits that the block builder or `process_transaction` then rejects (or the reverse), or a fee/size computation where `fee / tx_size` under- or over-counts due to a cast or a size mismatch between the decoded and re-encoded transaction. State whether the divergence lets an underpaying transaction be mined or a valid one be permanently un-mineable. Identity: the admissibility and fee a transaction is judged by in the mempool == the admissibility and fee it is judged by at block inclusion.",

    "High. DESERIALIZATION MUST ROUND-TRIP OR REJECT. `posttransaction.rs` decodes posted octets or JSON into a `StacksTransaction` via `consensus_deserialize`; the codec's `MAX_PAYLOAD_LEN`, the auth-field and payload deserializers, and `Value` serialization in contract-call arguments must accept exactly the transactions that re-serialize to the same bytes. Show input bytes that deserialize to a transaction whose re-serialization differs (so its txid or sighash is computed over different bytes than were transmitted), a trailing-bytes acceptance, a length field that under-reads a field, or a contract-call argument `Value` that deserializes past its declared type. Identity: for every accepted transaction, `consensus_deserialize` then `consensus_serialize` reproduces the original bytes, and the txid the network gossips == the txid the node stores.",

    "Critical. THE MISSING INVARIANT - what nobody built. Nothing asserts that the pre-signing hash covers the full set of executed fields across every future payload variant; nothing proves order-independent multisig cannot reuse a public-key slot as authority; the epoch gate trusts two independent codepaths (codec and stacks-transactions) to classify the same post-condition identically; the mempool fee floor and the block builder compute size from possibly-different encodings; a sponsored transaction splits nonce and fee authority across two accounts checked in two places. Identify the FIRST place one of these unstated authentication or accounting assumptions is violated by an unprivileged sender crafting their own transaction bytes, prove it with a Rust test in `stacks-codec` or `stackslib` that constructs the transaction, verifies the auth, applies it to a chainstate and asserts the authenticated fields versus the executed and charged fields, and show that once they diverge the transaction is either replayable, under-charged, or accepted by only part of the network.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate transaction-authentication and post-condition audit questions for one
    stacks-core target.

    ```
    target_file format:
    "'File Name: stacks-codec/src/transaction.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate blockchain-node security audit questions for this exact stacks-core target:

    {target_file}

    Project focus:
    stacks-core authenticates and applies Stacks transactions. Every transaction is bytes an
    unprivileged sender chose: an auth structure (singlesig, multisig, order-independent
    multisig, sponsored), a nonce, a fee, a chain id, a version, a payload and a
    post-condition list. The node decides (a) whether the recovered signer authorised
    exactly this transaction - `verify_origin` / `verify` rebuild the sighash via
    `next_signature` and recover the key; (b) whether every asset the transaction moves
    satisfies a `TransactionPostCondition` under its mode, comparing the codec's
    `FungibleConditionCode::check` against the VM's committed `AssetMap`; (c) whether the
    nonce advances once and the fee debited equals `get_tx_fee`. Anything executed or charged
    that the authenticated bytes did not commit, or an asset that moves past its
    post-conditions, or a classification two nodes disagree on, is the bug.

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (function, struct, enum variant, constant, trait) as they
      appear in the file.
    * EVERY question must close on an equality that must hold across a call. State it
      explicitly. Narrative questions with no stated equality are rejected.
    * Attacker is unprivileged only: any sender who can craft, sign and post arbitrary
      transaction bytes - any auth mode, any payload, any post-condition list, any nonce or
      fee - and mutate transactions signed by themselves. They may run a wallet and submit
      to any node's RPC.
    * Attacker is NOT a miner, signer, node operator or admin, and holds no other account's
      private key. No malicious peer, node or RPC beyond posting their own bytes; no
      compromised dependency; no social engineering.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - Block-assembly, tenure, reward-set and P2P internals beyond the mempool/RPC entry
        are handled in other variants and OUT OF SCOPE here, as are README, tests, benches
        and config.
      - Denial of service, gas griefing, block stuffing, mempool spam and memory hygiene are
        OUT OF SCOPE.
      - Defects in secp256k1, rusqlite or serde with no exploit path through this repo's
        auth/post-condition code are OUT OF SCOPE; a weakness here that steers them wrong is
        fully IN scope.
      - Also excluded: leaked keys, privileged accounts, centralization risk, best-practice
        notes, feature requests, oracle assumptions, funds sent by mistake, theoretical
        findings.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: forging or replaying a transaction so an asset moves without the owner's
      authorization; an asset moving past its post-conditions (theft); a transaction
      accepted by only part of the network (chain split, invalid-transaction processing);
      permanent freezing via an un-spendable-but-nonce-consuming replay.
      High: mempool-versus-block admissibility divergence that mines an underpaying tx or
      permanently blocks a valid one; a fee or nonce charged incorrectly; a txid/sighash
      computed over different bytes than transmitted.
    * Every question must be a concrete real-world scenario an unprivileged sender can
      execute by crafting and posting transaction bytes to a node.
    * A rejection is a finding only when it permanently blocks a valid transaction or a
      malformed one is accepted - say which.
    * Generate 20 to 40 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable with a Rust test in `stacks-codec` or `stackslib` on a
      local chainstate. Never propose testing on mainnet or a public testnet.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO values that must be equal and ask whether they are:
      transaction authenticated and transaction executed, signatures verified and threshold
      required, asset moved and post-condition permitted, fee/nonce charged and committed,
      admissibility on node A and node B.

    Known dead ends - do NOT generate questions about these:
    * Anything needing a miner, signer, admin or another account's private key.
    * A CVE in a dependency with no reachable path through this repo's auth code.
    * Mempool spam, DoS, or a sender harming only their own account.
    * Findings only reproducible through tests or tooling.

    Core equalities (each question must close on one):
    * AUTHENTICATION: the transaction the recovered key(s) signed == the transaction
      executed and charged.
    * THRESHOLD: distinct verified signatures over the correct sighash == signatures_required.
    * POST-CONDITION: every committed asset movement == a movement its post-conditions permit.
    * ACCOUNTING: nonce advances once; fee debited == get_tx_fee; balance change == authorized.
    * DETERMINISM: admissibility and classification on node A == on node B at one tip.

    Each question must include:
    1. target function, struct or enum variant;
    2. attacker action (a concrete transaction with the auth/payload/post-condition fields
       that matter);
    3. preconditions (epoch, account state, nonce, balances);
    4. call sequence through the codec, auth and transactions-db path;
    5. the equality that breaks, written explicitly;
    6. scoped impact and whose funds are exposed;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Method: function_or_struct] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, breaking the equality EQUALITY, causing scoped impact: SCOPE_IMPACT against PARTY? Proof idea: Rust test PARAMETERS asserting AUTHENTICATION, THRESHOLD, POST_CONDITION, ACCOUNTING, or DETERMINISM.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a transaction-authentication and post-condition exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: any sender who can craft, sign, mutate (only transactions they signed) and post arbitrary transaction bytes to a node's RPC. They are not a miner, signer, node operator or admin, and hold no other account's private key.
- Reject malicious peer/node/RPC assumptions beyond posting their own bytes, compromised dependencies, social engineering, and any path requiring a privileged role.
- OUT OF SCOPE, reject on sight: block-assembly, tenure, reward-set and P2P internals beyond the mempool/RPC entry; README, tests, benches, config; denial of service, gas griefing, block stuffing, mempool spam and memory hygiene; secp256k1, rusqlite or serde defects with no exploit path through this repo's auth/post-condition code; oracle assumptions; funds sent by mistake; best-practice notes; theoretical findings.
- The impact must be one of: Critical - forging or replaying a transaction so an asset moves without authorization, an asset moving past its post-conditions, a transaction accepted by only part of the network, permanent freezing via a nonce-consuming replay; High - mempool-versus-block admissibility divergence, a fee or nonce charged incorrectly, a txid/sighash over different bytes than transmitted.
- Focus on real impact: a field executed that the signature did not cover, an asset that escaped its post-conditions, or two nodes disagreeing on one transaction.

## Validate
- Write the equality the question claims is broken between two named values BEFORE tracing any code.
- Trace the exact reachable path from the attacker's bytes and record every read and write of the sighash, recovered pubkey, `signer` hash, `signatures_required`, the post-condition list and mode, the committed `AssetMap`, the nonce and the fee.
- Evaluate both sides of the equality before and after. If they still match, output no vulnerability.
- Check whether `verify_origin`/`verify`, `next_signature`, the multisig field counting, the low-S verification mode, `check_transaction_postconditions`, `process_transaction_precheck`, the epoch gate, or `check_transaction_nonces` already prevents the divergence.
- State what the attacker gains per transaction and whether it is repeatable.
- Require exact file/function support and a reproducible Rust test on a local chainstate.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The broken equality, the code path, root cause, the attacker's exact transaction, exploit flow, and why existing guards fail]

### Impact Explanation
[What is forged, moved, replayed, misconfigured or split, which party, repeatability, matching severity category]

### Likelihood Explanation
[Preconditions, epoch and account state required, attacker cost, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Rust test plan with the exact assertions on both sides of the equality]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for stacks-core auth/post-condition claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- A claim is only valid if the report states the broken equality between two named values and shows both sides concretely. Reject prose-only claims.
- Reject anything requiring a miner, signer, node operator, admin, another account's private key, a malicious peer/node/RPC beyond posting bytes, a compromised dependency, or social engineering.
- OUT OF SCOPE, reject on sight: block-assembly, tenure, reward-set and P2P internals beyond the mempool/RPC entry; README, tests, benches, config; denial of service, gas griefing, block stuffing, mempool spam and memory hygiene; secp256k1, rusqlite or serde defects with no exploit path through this repo's auth/post-condition code; oracle assumptions; centralization risk; funds sent by mistake; best-practice notes; feature requests; theoretical findings.
- The impact must be one of: Critical - forging or replaying a transaction so an asset moves without authorization, an asset moving past its post-conditions, a transaction accepted by only part of the network, permanent freezing via a nonce-consuming replay; High - mempool-versus-block admissibility divergence, a fee or nonce charged incorrectly, a txid/sighash over different bytes than transmitted.
- Reject claims where the only loss is the attacker's own account.
- Reject if the bug was already fixed, publicly disclosed, or covered by a known-issues list.
- A valid report must be triggerable by an unprivileged sender against the current code by posting their own transaction bytes.
- A PoC is mandatory. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function/struct/enum, and line references.
2. The equality written explicitly, with both sides shown before and after.
3. Clear root cause: which uncovered field, miscounted signature, malleable signature, escaped post-condition, epoch-gate divergence, or nonce/fee error causes it.
4. Reachable exploit path: preconditions -> attacker bytes -> codec, auth and transactions-db sequence -> observed divergence.
5. `verify_origin`/`verify`, `next_signature`, the multisig counting, the low-S mode, `check_transaction_postconditions`, the epoch gate and `check_transaction_nonces` reviewed and shown insufficient.
6. Impact stated concretely: which funds or which nodes, and whether it is repeatable.
7. Reproducible proof: Rust test on a local chainstate with the asserted values.

## Silent Triage Questions
Before output, internally answer:
- What exactly is the equality, and does it actually fail?
- Can an ordinary sender trigger it with no privileged role and no other user's key?
- Is the flaw in this repo's auth/post-condition/codec code, not in a dependency or a wallet?
- What is forged, moved, replayed or split, whose funds are exposed, and can it be repeated?
- Would an Immunefi triager accept the exploit path under the Blockchain/DLT severity system?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the broken equality and impact]

## Finding Description
[Exact code path, the equality, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[What is forged, moved, replayed or split, affected party, repeatability, severity category]

## Likelihood Explanation
[Attacker capability, preconditions, state required, cost, feasibility]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Rust test plan with concrete assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for stacks-core transaction auth.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope repo context only (stacks-codec transaction.rs, auth.rs, transactions.rs, accounts.rs, the secp256k1 and address modules, the post-condition VM/codec code, the mempool and posttransaction entry). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-sender analogs that break an equality: a transaction executed or charged beyond what its signature covered, signatures verified fewer than the threshold, an asset moving past its post-conditions, a fee/nonce charged wrong, or a transaction classified differently by two nodes.
- OUT OF SCOPE, reject on sight: block-assembly, tenure, reward-set and P2P internals beyond the mempool/RPC entry; README, tests, benches, config; denial of service, gas griefing, block stuffing, mempool spam and memory hygiene; secp256k1, rusqlite or serde defects with no exploit path through this repo's auth code; anything requiring a miner, signer, admin or another account's key; malicious peer/node assumptions beyond posting bytes; oracle assumptions; funds sent by mistake; best-practice notes; theoretical findings.
- The impact must be one of: Critical - forging or replaying a transaction so an asset moves without authorization, an asset moving past its post-conditions, a transaction accepted by only part of the network, permanent freezing via a nonce-consuming replay; High - mempool-versus-block admissibility divergence, a fee or nonce charged incorrectly, a txid/sighash over different bytes than transmitted.
- Reject analogs where the only loss is the attacker's own account.

## Validate
- Map the bug class to the strongest reachable path in this repo and state the equality it would break.
- Evaluate both sides before and after the attacker's transaction.
- Prove root cause with exact file/function support.
- Accept only concrete forgery, replay, post-condition escape, mis-charged fee/nonce, or cross-node divergence.

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
