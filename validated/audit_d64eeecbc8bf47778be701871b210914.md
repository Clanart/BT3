[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-experimental/sources/large_packages.move (L155-178)
```text
        let staging_area = borrow_global_mut<StagingArea>(owner_address);

        if (!metadata_chunk.is_empty()) {
            staging_area.metadata_serialized.append(metadata_chunk);
        };

        let i = 0;
        while (i < code_chunks.length()) {
            let inner_code = code_chunks[i];
            let idx = (code_indices[i] as u64);

            if (staging_area.code.contains(idx)) {
                staging_area.code.borrow_mut(idx).append(inner_code);
            } else {
                staging_area.code.add(idx, inner_code);
                if (idx > staging_area.last_module_idx) {
                    staging_area.last_module_idx = idx;
                }
            };
            i += 1;
        };

        staging_area
    }
```
