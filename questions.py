import json
import os

MAX_REPO = 40
SOURCE_REPO = 'anza-xyz/agave'
REPO_NAME = 'agave'
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
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
    'accounts-db/src/account_info.rs',
    'accounts-db/src/account_locks.rs',
    'accounts-db/src/account_storage/stored_account_info.rs',
    'accounts-db/src/account_storage.rs',
    'accounts-db/src/account_storage_entry.rs',
    'accounts-db/src/account_storage_reader.rs',
    'accounts-db/src/accounts.rs',
    'accounts-db/src/accounts_cache.rs',
    'accounts-db/src/accounts_db/accounts_db_config.rs',
    'accounts-db/src/accounts_db/stats.rs',
    'accounts-db/src/accounts_db.rs',
    'accounts-db/src/accounts_file.rs',
    'accounts-db/src/accounts_hash.rs',
    'accounts-db/src/accounts_index/account_map_entry.rs',
    'accounts-db/src/accounts_index/accounts_index_storage.rs',
    'accounts-db/src/accounts_index/bucket_map_holder.rs',
    'accounts-db/src/accounts_index/in_mem_accounts_index.rs',
    'accounts-db/src/accounts_index/iter.rs',
    'accounts-db/src/accounts_index/secondary.rs',
    'accounts-db/src/accounts_index/stats.rs',
    'accounts-db/src/accounts_index.rs',
    'accounts-db/src/accounts_scan.rs',
    'accounts-db/src/active_stats.rs',
    'accounts-db/src/ancestors.rs',
    'accounts-db/src/ancient_append_vecs.rs',
    'accounts-db/src/append_vec/meta.rs',
    'accounts-db/src/append_vec.rs',
    'accounts-db/src/blockhash_queue.rs',
    'accounts-db/src/contains.rs',
    'accounts-db/src/is_loadable.rs',
    'accounts-db/src/is_zero_lamport.rs',
    'accounts-db/src/lib.rs',
    'accounts-db/src/obsolete_accounts.rs',
    'accounts-db/src/partitioned_rewards.rs',
    'accounts-db/src/pubkey_bins.rs',
    'accounts-db/src/read_only_accounts_cache.rs',
    'accounts-db/src/rolling_bit_field/iterators.rs',
    'accounts-db/src/rolling_bit_field.rs',
    'accounts-db/src/sorted_storages.rs',
    'accounts-db/src/stake_rewards.rs',
    'accounts-db/src/storable_accounts.rs',
    'accounts-db/src/utils.rs',
    'accounts-db/src/waitable_condvar.rs',
    'builtins/src/core_bpf_migration.rs',
    'builtins/src/lib.rs',
    'builtins/src/prototype.rs',
    'compute-budget/src/compute_budget.rs',
    'compute-budget/src/compute_budget_limits.rs',
    'compute-budget/src/lib.rs',
    'compute-budget-instruction/src/builtin_programs_filter.rs',
    'compute-budget-instruction/src/compute_budget_instruction_details.rs',
    'compute-budget-instruction/src/compute_budget_program_id_filter.rs',
    'compute-budget-instruction/src/instructions_processor.rs',
    'compute-budget-instruction/src/lib.rs',
    'connection-cache/src/client_connection.rs',
    'connection-cache/src/connection_cache.rs',
    'connection-cache/src/connection_cache_stats.rs',
    'connection-cache/src/lib.rs',
    'connection-cache/src/nonblocking/client_connection.rs',
    'connection-cache/src/nonblocking/mod.rs',
    'core/src/banking_stage/committer.rs',
    'core/src/banking_stage/consume_worker.rs',
    'core/src/banking_stage/consumer.rs',
    'core/src/banking_stage/decision_maker.rs',
    'core/src/banking_stage/latest_validator_vote_packet.rs',
    'core/src/banking_stage/leader_slot_metrics.rs',
    'core/src/banking_stage/leader_slot_timing_metrics.rs',
    'core/src/banking_stage/progress_tracker.rs',
    'core/src/banking_stage/scheduler_messages.rs',
    'core/src/banking_stage/tpu_to_pack.rs',
    'core/src/banking_stage/transaction_scheduler/batch_id_generator.rs',
    'core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs',
    'core/src/banking_stage/transaction_scheduler/in_flight_tracker.rs',
    'core/src/banking_stage/transaction_scheduler/mod.rs',
    'core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs',
    'core/src/banking_stage/transaction_scheduler/scheduler.rs',
    'core/src/banking_stage/transaction_scheduler/scheduler_common.rs',
    'core/src/banking_stage/transaction_scheduler/scheduler_controller.rs',
    'core/src/banking_stage/transaction_scheduler/scheduler_error.rs',
    'core/src/banking_stage/transaction_scheduler/scheduler_metrics.rs',
    'core/src/banking_stage/transaction_scheduler/transaction_priority_id.rs',
    'core/src/banking_stage/transaction_scheduler/transaction_state.rs',
    'core/src/banking_stage/transaction_scheduler/transaction_state_container.rs',
    'core/src/banking_stage/vote_packet_receiver.rs',
    'core/src/banking_stage/vote_storage.rs',
    'core/src/banking_stage/vote_worker.rs',
    'core/src/banking_stage.rs',
    'core/src/banking_trace.rs',
    'core/src/block_creation_loop/rewards/certs_builder/entry/notar_entry.rs',
    'core/src/block_creation_loop/rewards/certs_builder/entry/partial_cert.rs',
    'core/src/block_creation_loop/rewards/certs_builder/entry.rs',
    'core/src/block_creation_loop/rewards/certs_builder.rs',
    'core/src/block_creation_loop/rewards/certs_requestor.rs',
    'core/src/block_creation_loop/rewards/mod.rs',
    'core/src/block_creation_loop/rewards/msg_types.rs',
    'core/src/block_creation_loop/rewards/reward_certs_service.rs',
    'core/src/block_creation_loop/stats.rs',
    'core/src/block_creation_loop.rs',
    'core/src/cluster_info_vote_listener.rs',
    'core/src/cluster_slots_service/cluster_slots.rs',
    'core/src/cluster_slots_service/slot_supporters.rs',
    'core/src/cluster_slots_service.rs',
    'core/src/commitment_service.rs',
    'core/src/completed_data_sets_service.rs',
    'core/src/consensus/fork_choice.rs',
    'core/src/consensus/heaviest_subtree_fork_choice.rs',
    'core/src/consensus/latest_validator_votes_for_frozen_banks.rs',
    'core/src/consensus/progress_map.rs',
    'core/src/consensus/tower1_14_11.rs',
    'core/src/consensus/tower1_7_14.rs',
    'core/src/consensus/tower_storage.rs',
    'core/src/consensus/tower_vote_state.rs',
    'core/src/consensus/tree_diff.rs',
    'core/src/consensus/vote_stake_tracker.rs',
    'core/src/consensus.rs',
    'core/src/cost_update_service.rs',
    'core/src/drop_bank_service.rs',
    'core/src/epoch_specs.rs',
    'core/src/fetch_stage.rs',
    'core/src/forwarding_stage/packet_container.rs',
    'core/src/forwarding_stage.rs',
    'core/src/lib.rs',
    'core/src/next_leader.rs',
    'core/src/optimistic_confirmation_verifier.rs',
    'core/src/repair/ancestor_hashes_service.rs',
    'core/src/repair/block_id_repair_service/stats.rs',
    'core/src/repair/block_id_repair_service.rs',
    'core/src/repair/cluster_slot_state_verifier.rs',
    'core/src/repair/duplicate_repair_status.rs',
    'core/src/repair/malicious_repair_handler.rs',
    'core/src/repair/mod.rs',
    'core/src/repair/outstanding_requests.rs',
    'core/src/repair/packet_threshold.rs',
    'core/src/repair/repair_generic_traversal.rs',
    'core/src/repair/repair_handler.rs',
    'core/src/repair/repair_response.rs',
    'core/src/repair/repair_service.rs',
    'core/src/repair/repair_weight.rs',
    'core/src/repair/repair_weighted_traversal.rs',
    'core/src/repair/request_response.rs',
    'core/src/repair/result.rs',
    'core/src/repair/serve_repair.rs',
    'core/src/repair/serve_repair_service.rs',
    'core/src/repair/standard_repair_handler.rs',
    'core/src/replay_stage/dead_slots.rs',
    'core/src/replay_stage/update_parent.rs',
    'core/src/replay_stage.rs',
    'core/src/resource_limits.rs',
    'core/src/result.rs',
    'core/src/shred_fetch_stage.rs',
    'core/src/sigverify.rs',
    'core/src/sigverify_stage.rs',
    'core/src/staked_nodes_updater_service.rs',
    'core/src/stats_reporter_service.rs',
    'core/src/system_monitor_service.rs',
    'core/src/tpu.rs',
    'core/src/tpu_entry_notifier.rs',
    'core/src/transaction_priority.rs',
    'core/src/tvu.rs',
    'core/src/unfrozen_gossip_verified_vote_hashes.rs',
    'core/src/validator.rs',
    'core/src/voting_service.rs',
    'core/src/warm_quic_cache_service.rs',
    'core/src/window_service.rs',
    'cost-model/src/block_cost_limits.rs',
    'cost-model/src/cost_model.rs',
    'cost-model/src/cost_tracker.rs',
    'cost-model/src/cost_tracker_post_analysis.rs',
    'cost-model/src/lib.rs',
    'cost-model/src/shred_limit.rs',
    'cost-model/src/transaction_cost.rs',
    'entry/src/block_component.rs',
    'entry/src/entry.rs',
    'entry/src/entry_or_marker.rs',
    'entry/src/lib.rs',
    'entry/src/poh.rs',
    'feature-set/src/lib.rs',
    'gossip/src/cluster_info.rs',
    'gossip/src/cluster_info_metrics.rs',
    'gossip/src/contact_info.rs',
    'gossip/src/contact_info_notifier.rs',
    'gossip/src/crds.rs',
    'gossip/src/crds_data.rs',
    'gossip/src/crds_entry.rs',
    'gossip/src/crds_filter.rs',
    'gossip/src/crds_gossip.rs',
    'gossip/src/crds_gossip_error.rs',
    'gossip/src/crds_gossip_pull.rs',
    'gossip/src/crds_gossip_push.rs',
    'gossip/src/crds_shards.rs',
    'gossip/src/crds_value.rs',
    'gossip/src/duplicate_shred.rs',
    'gossip/src/duplicate_shred_handler.rs',
    'gossip/src/duplicate_shred_listener.rs',
    'gossip/src/epoch_slots.rs',
    'gossip/src/epoch_specs.rs',
    'gossip/src/gossip_error.rs',
    'gossip/src/gossip_service.rs',
    'gossip/src/lib.rs',
    'gossip/src/node.rs',
    'gossip/src/ping_pong.rs',
    'gossip/src/protocol.rs',
    'gossip/src/push_active_set.rs',
    'gossip/src/received_cache.rs',
    'gossip/src/restart_crds_values.rs',
    'gossip/src/sigverify_cache.rs',
    'gossip/src/tlv.rs',
    'gossip/src/weighted_shuffle.rs',
    'ledger/src/ancestor_iterator.rs',
    'ledger/src/bank_forks_utils.rs',
    'ledger/src/bit_vec.rs',
    'ledger/src/block_error.rs',
    'ledger/src/blockstore/blockstore_purge.rs',
    'ledger/src/blockstore/cleanup_service.rs',
    'ledger/src/blockstore/column.rs',
    'ledger/src/blockstore/error.rs',
    'ledger/src/blockstore.rs',
    'ledger/src/blockstore_db.rs',
    'ledger/src/blockstore_meta.rs',
    'ledger/src/blockstore_metrics.rs',
    'ledger/src/blockstore_options.rs',
    'ledger/src/blockstore_processor.rs',
    'ledger/src/genesis_utils.rs',
    'ledger/src/leader_schedule_cache.rs',
    'ledger/src/lib.rs',
    'ledger/src/next_slots_iterator.rs',
    'ledger/src/rooted_slot_iterator.rs',
    'ledger/src/shred/common.rs',
    'ledger/src/shred/filter.rs',
    'ledger/src/shred/merkle.rs',
    'ledger/src/shred/merkle_tree.rs',
    'ledger/src/shred/payload.rs',
    'ledger/src/shred/shred_code.rs',
    'ledger/src/shred/shred_data.rs',
    'ledger/src/shred/stats.rs',
    'ledger/src/shred/traits.rs',
    'ledger/src/shred/wire.rs',
    'ledger/src/shred.rs',
    'ledger/src/shredder.rs',
    'ledger/src/sigverify_shreds.rs',
    'ledger/src/slot_stats.rs',
    'ledger/src/staking_utils.rs',
    'ledger/src/transaction_address_lookup_table_scanner.rs',
    'net-utils/src/banlist.rs',
    'net-utils/src/lib.rs',
    'net-utils/src/multihomed_sockets.rs',
    'net-utils/src/pinned_xdp_sender.rs',
    'net-utils/src/socket_addr_space.rs',
    'net-utils/src/sockets.rs',
    'net-utils/src/token_bucket.rs',
    'perf/src/data_budget.rs',
    'perf/src/deduper.rs',
    'perf/src/lib.rs',
    'perf/src/packet.rs',
    'perf/src/recycled_vec.rs',
    'perf/src/recycler.rs',
    'perf/src/sigverify.rs',
    'perf/src/thread.rs',
    'poh/src/lib.rs',
    'poh/src/poh_controller.rs',
    'poh/src/poh_recorder.rs',
    'poh/src/poh_service.rs',
    'poh/src/record_channels.rs',
    'poh/src/transaction_recorder.rs',
    'precompiles/src/ed25519.rs',
    'precompiles/src/lib.rs',
    'precompiles/src/secp256k1.rs',
    'precompiles/src/secp256r1.rs',
    'program-runtime/src/cpi.rs',
    'program-runtime/src/deploy.rs',
    'program-runtime/src/execution_budget.rs',
    'program-runtime/src/invoke_context.rs',
    'program-runtime/src/lib.rs',
    'program-runtime/src/loaded_programs.rs',
    'program-runtime/src/loading_task.rs',
    'program-runtime/src/mem_pool.rs',
    'program-runtime/src/memory.rs',
    'program-runtime/src/memory_context.rs',
    'program-runtime/src/program_cache_entry.rs',
    'program-runtime/src/program_metrics.rs',
    'program-runtime/src/serialization.rs',
    'program-runtime/src/stable_log.rs',
    'program-runtime/src/sysvar_cache.rs',
    'programs/bpf_loader/src/lib.rs',
    'programs/compute-budget/src/lib.rs',
    'programs/system/src/lib.rs',
    'programs/system/src/system_instruction.rs',
    'programs/system/src/system_processor.rs',
    'programs/vote/src/lib.rs',
    'programs/vote/src/vote_processor.rs',
    'programs/vote/src/vote_state/handler.rs',
    'programs/vote/src/vote_state/mod.rs',
    'rpc/src/cluster_tpu_info.rs',
    'rpc/src/filter.rs',
    'rpc/src/lib.rs',
    'rpc/src/max_slots.rs',
    'rpc/src/optimistically_confirmed_bank_tracker.rs',
    'rpc/src/parsed_token_accounts.rs',
    'rpc/src/rpc/account_resolver.rs',
    'rpc/src/rpc.rs',
    'rpc/src/rpc_cache.rs',
    'rpc/src/rpc_completed_slots_service.rs',
    'rpc/src/rpc_pubsub.rs',
    'rpc/src/rpc_pubsub_service.rs',
    'rpc/src/rpc_service.rs',
    'rpc/src/rpc_subscription_tracker.rs',
    'rpc/src/rpc_subscriptions.rs',
    'rpc/src/slot_status_notifier.rs',
    'rpc/src/transaction_notifier_interface.rs',
    'rpc/src/transaction_status_service.rs',
    'runtime/src/account_saver.rs',
    'runtime/src/accounts_background_service.rs',
    'runtime/src/bank/accounts_lt_hash.rs',
    'runtime/src/bank/address_lookup_table.rs',
    'runtime/src/bank/bank_hash_details.rs',
    'runtime/src/bank/builtins/core_bpf_migration/error.rs',
    'runtime/src/bank/builtins/core_bpf_migration/mod.rs',
    'runtime/src/bank/builtins/core_bpf_migration/source_buffer.rs',
    'runtime/src/bank/builtins/core_bpf_migration/target_bpf_v2.rs',
    'runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs',
    'runtime/src/bank/builtins/core_bpf_migration/target_core_bpf.rs',
    'runtime/src/bank/builtins/mod.rs',
    'runtime/src/bank/check_transactions.rs',
    'runtime/src/bank/entry_bytes_budget.rs',
    'runtime/src/bank/fee_distribution.rs',
    'runtime/src/bank/metrics.rs',
    'runtime/src/bank/partitioned_epoch_rewards/calculation.rs',
    'runtime/src/bank/partitioned_epoch_rewards/distribution.rs',
    'runtime/src/bank/partitioned_epoch_rewards/epoch_rewards_hasher.rs',
    'runtime/src/bank/partitioned_epoch_rewards/mod.rs',
    'runtime/src/bank/partitioned_epoch_rewards/sysvar.rs',
    'runtime/src/bank/recent_blockhashes_account.rs',
    'runtime/src/bank/sysvar_cache.rs',
    'runtime/src/bank.rs',
    'runtime/src/bank_client.rs',
    'runtime/src/bank_forks.rs',
    'runtime/src/bank_forks_controller.rs',
    'runtime/src/bank_utils.rs',
    'runtime/src/block_component_processor/vote_reward/epoch_inflation_account_state.rs',
    'runtime/src/block_component_processor/vote_reward.rs',
    'runtime/src/block_component_processor.rs',
    'runtime/src/commitment.rs',
    'runtime/src/dependency_tracker.rs',
    'runtime/src/epoch_stakes.rs',
    'runtime/src/genesis_utils.rs',
    'runtime/src/inflation_rewards/mod.rs',
    'runtime/src/inflation_rewards/points.rs',
    'runtime/src/installed_scheduler_pool.rs',
    'runtime/src/leader_schedule_utils.rs',
    'runtime/src/lib.rs',
    'runtime/src/loader_utils.rs',
    'runtime/src/non_circulating_supply.rs',
    'runtime/src/prioritization_fee.rs',
    'runtime/src/prioritization_fee_cache.rs',
    'runtime/src/read_optimized_dashmap.rs',
    'runtime/src/rent_collector.rs',
    'runtime/src/reward_info.rs',
    'runtime/src/runtime_config.rs',
    'runtime/src/slot_params.rs',
    'runtime/src/stake_account.rs',
    'runtime/src/stake_delegation.rs',
    'runtime/src/stake_history.rs',
    'runtime/src/stake_utils.rs',
    'runtime/src/stake_weighted_timestamp.rs',
    'runtime/src/stakes/serde_stakes.rs',
    'runtime/src/stakes.rs',
    'runtime/src/static_ids.rs',
    'runtime/src/status_cache.rs',
    'runtime/src/sysvar_account.rs',
    'runtime/src/transaction_balances.rs',
    'runtime/src/transaction_batch.rs',
    'runtime/src/transaction_execution.rs',
    'runtime/src/validated_block_finalization.rs',
    'runtime/src/validated_reward_certificate.rs',
    'runtime/src/vote_sender_types.rs',
    'runtime-transaction/src/instruction_data_len.rs',
    'runtime-transaction/src/instruction_meta.rs',
    'runtime-transaction/src/lib.rs',
    'runtime-transaction/src/runtime_transaction/sdk_transactions.rs',
    'runtime-transaction/src/runtime_transaction/transaction_view.rs',
    'runtime-transaction/src/runtime_transaction.rs',
    'runtime-transaction/src/sanitize_config.rs',
    'runtime-transaction/src/signature_details.rs',
    'runtime-transaction/src/transaction_meta.rs',
    'runtime-transaction/src/transaction_with_meta.rs',
    'send-transaction-service/src/lib.rs',
    'send-transaction-service/src/send_transaction_service.rs',
    'send-transaction-service/src/send_transaction_service_stats.rs',
    'send-transaction-service/src/tpu_info.rs',
    'send-transaction-service/src/transaction_client.rs',
    'streamer/src/evicting_sender.rs',
    'streamer/src/lib.rs',
    'streamer/src/msghdr.rs',
    'streamer/src/nonblocking/connection_rate_limiter.rs',
    'streamer/src/nonblocking/mod.rs',
    'streamer/src/nonblocking/qos.rs',
    'streamer/src/nonblocking/quic.rs',
    'streamer/src/nonblocking/simple_qos.rs',
    'streamer/src/nonblocking/stream_throttle.rs',
    'streamer/src/nonblocking/swqos.rs',
    'streamer/src/packet.rs',
    'streamer/src/quic.rs',
    'streamer/src/quic_socket.rs',
    'streamer/src/recvmmsg.rs',
    'streamer/src/sendmmsg.rs',
    'streamer/src/streamer.rs',
    'svm/src/account_loader.rs',
    'svm/src/account_overrides.rs',
    'svm/src/lib.rs',
    'svm/src/nonce_info.rs',
    'svm/src/program_loader.rs',
    'svm/src/rent_calculator.rs',
    'svm/src/rollback_accounts.rs',
    'svm/src/transaction_account_state_info.rs',
    'svm/src/transaction_balances.rs',
    'svm/src/transaction_commit_result.rs',
    'svm/src/transaction_error_metrics.rs',
    'svm/src/transaction_execution_result.rs',
    'svm/src/transaction_processing_callback.rs',
    'svm/src/transaction_processing_result.rs',
    'svm/src/transaction_processor.rs',
    'syscalls/src/cpi.rs',
    'syscalls/src/lib.rs',
    'syscalls/src/logging.rs',
    'syscalls/src/mem_ops.rs',
    'syscalls/src/sysvar.rs',
    'transaction-context/src/instruction.rs',
    'transaction-context/src/instruction_accounts.rs',
    'transaction-context/src/lib.rs',
    'transaction-context/src/transaction.rs',
    'transaction-context/src/transaction_accounts.rs',
    'transaction-context/src/vm_addresses.rs',
    'transaction-context/src/vm_slice.rs',
    'turbine/src/addr_cache.rs',
    'turbine/src/broadcast_stage/broadcast_duplicates_run.rs',
    'turbine/src/broadcast_stage/broadcast_metrics.rs',
    'turbine/src/broadcast_stage/broadcast_utils.rs',
    'turbine/src/broadcast_stage/standard_broadcast_run.rs',
    'turbine/src/broadcast_stage.rs',
    'turbine/src/cluster_nodes.rs',
    'turbine/src/lib.rs',
    'turbine/src/retransmit_stage.rs',
    'turbine/src/sigverify_shreds.rs',
]

target_scopes = [
    'Critical. An unprivileged attacker can steal lamports, fees, rewards, stake, vote funds, or program-controlled balances without the victim or rightful authority consenting.',
    'Critical. An unprivileged attacker can make a validator accept, root, optimistically confirm, execute, or cache a state transition that honest code should reject, causing a consensus or safety violation.',
    'Critical. An unprivileged attacker can halt validator progress or require human intervention through production transaction, QUIC/TPU, gossip, shred, repair, blockstore, or runtime paths.',
    'Critical. An unprivileged attacker can remotely exhaust memory, CPU, disk, or crash the validator through non-RPC public protocols such as QUIC/TPU, gossip, shred, repair, or blockstore ingestion.',
    'High. A single unprivileged client can crash or materially degrade the built-in RPC or pubsub service while staying within the published single-client rate limits.',
    'High. An unprivileged attacker can replay, double-apply, or permanently lock a transaction, nonce, receipt, reward, withdrawal, or account-state transition in production paths.',
]

HYPERBRIDGE_ALLOWED_IMPACT_SCOPE = """Valid only: unprivileged Agave issues in transactions/CPI,
RPC/pubsub, QUIC/TPU, gossip, shreds, repair, blockstore, runtime, accounts, or built-ins that
cause fund theft/loss, false execution/rooting/acceptance, consensus halt, non-RPC remote
exhaustion/crash, or single-client low-rate RPC crash/degradation. Reject malicious
peer/node/validator assumptions, trusted plugins/processes, snapshots/bootstrap-only, metrics,
dependencies, docs/tests/mocks/generated/`.toml`, Loader V4, Alpenglow-only, VM interpreter-only,
and excluded RPC patterns."""

HYPERBRIDGE_AUDIT_PIVOTS = """Focus on broken signer/writable/authority/lamport/nonce/receipt/
one-time-execution invariants, false bank/root/cache state, unauthorized fund movement, duplicate
application, stale state use, bad accounting, or single-client service exhaustion."""


def question_generator(target_file: str) -> str:
    """
    Generate security questions for one Agave target.
    """

    prompt = f"""
    Draft 18 to 24 Agave exploit questions for:
    {target_file}

    Use only real unprivileged entrypoints: transactions/CPI, built-ins, RPC/pubsub, QUIC/TPU,
    gossip, shreds, repair, blockstore ingest, runtime, and account caches.

    {HYPERBRIDGE_ALLOWED_IMPACT_SCOPE}
    {HYPERBRIDGE_AUDIT_PIVOTS}

    Rules:
    * `File Name:` = this file.
    * `Scope:` = exactly one `target_scopes` item.
    * Use repo context only.
    * No admin/validator/malicious peer-node/trusted-plugin/leaked-key/off-repo assumptions.
    * Ignore tests, mocks, docs, generated files, `.toml`, dependencies, Loader V4, Alpenglow-only, snapshots/bootstrap-only, and front-run-only ideas.
    * Name the exact wrong value and keep each question immediately testable.

    Return Python only.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_type] Can attacker-controlled INPUT through PUBLIC_ENTRYPOINT under REQUIRED_STATE reach TARGET_PATH and break INVARIANT, corrupting EXACT_VALUE with scoped impact SCOPE_IMPACT? Proof idea: focused repo test.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Agave exploit-question validation prompt.
    """
    return f"""# AGAVE REVIEW

## Submitted Question
{question}

## Scope
Only Agave production code. Only unprivileged public inputs. Reject malicious peers/nodes,
trusted integrations, front-run-only claims, and excluded bounty families.

## Valid Impact
{HYPERBRIDGE_ALLOWED_IMPACT_SCOPE}

## Review Path
1. Trace the exact public-input path.
2. Compare intended vs actual signer/account/nonce/receipt/slot/root/hash/balance/resource result.
3. Name the wrong value exactly.
4. Reject if existing checks already stop it.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a cross-project analog scan prompt for Agave issues.
    """
    prompt = f"""# AGAVE ANALOG SCAN

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Find a real Agave analog from local code only.

## Valid Impact
{HYPERBRIDGE_ALLOWED_IMPACT_SCOPE}

## Method
- Reduce the report to its broken invariant and attacker primitive.
- Keep only the strongest Agave path with exact file/function support.
- Reject malicious peer/node/validator/admin/trusted-integration/leaked-key/front-run-only assumptions.
- Name the exact corrupted value and show why existing guards do not stop the path.
- Either produce a concrete Agave issue from local code evidence or return `#NoVulnerability found for this question.`

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
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict Agave validation prompt for security claims.
    """
    prompt = f"""# AGAVE CLAIM VALIDATION

## Security Claim
{report}

## Rules
- Validate only the submitted claim against Agave production code.
- Do not widen the claim or raise severity without evidence.
- The attacker must be unprivileged and use public inputs.
- Reject malicious peer/node/validator, trusted plugin/process, leaked key, privileged control, front-run-only, snapshots, Loader V4, Alpenglow-only, test/mock/docs/generated/`.toml`.
- The final impact must match one `target_scopes` item and name the exact corrupted value.

## Valid Impact
{HYPERBRIDGE_ALLOWED_IMPACT_SCOPE}

## Required Checks
1. Exact file and function references in scoped code.
2. A clear invariant tied to signer/account correctness, one-time execution, slot/root/hash acceptance, resource bounds, or fund/accounting correctness.
3. A reachable exploit path from attacker input to bad state, bad execution, bad rooting, bad payout, duplicate application, or crash/halt.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: lamports, authority, nonce, receipt, slot/root/hash, account view, cache state, shred/blockstore state, or resource bound.
6. A reproducible proof path via Rust unit, integration, property, or fuzz-style testing.

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
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
