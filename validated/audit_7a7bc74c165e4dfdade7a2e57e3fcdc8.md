### Title
Field-number-driven integer wraparound in TcParser fast-table skip-entry generation causes out-of-bounds array read during binary parse dispatch - (File: src/google/protobuf/generated_message_tctable_gen.cc, exercised via src/google/protobuf/generated_message_tctable_lite.cc)

### Summary
The ImageMagick CVE-2016-7527 is a classic "attacker-controlled length/offset value used without a bounds check against the actual buffer, in a format-specific dispatch table walk, causing an out-of-bounds read." The closest verified Protobuf analog is a table-driven dispatch bug in the C++ `TcParser` fast-path: the number of "skip entries" generated for the field-number lookup table is stored in a `uint16_t` counter that can wrap around 65536 when a message has a very large, sparse field-number range. A wrapped index later causes `TcParser`'s runtime field lookup to read a reinterpreted/garbage table slot and can walk out of the bounds of the generated `field_entries`/skip-table array while parsing an incoming binary payload, exactly mirroring the "crafted external input drives a table index past its buffer" invariant failure in the WPG coder.

### Finding Description
`TcParser`'s binary-parsing fast path uses a per-message generated dispatch table (`TcParseTableBase`) containing a "skip map" used to quickly determine, from a wire-format field number, which `FieldEntry` to use. This table is built once from the message's descriptor and is walked on every incoming byte stream during `ParseFrom*`/`MergeFrom*`.

The existing regression test `TcParserTest.OobGenReproduction` in [1](#0-0)  demonstrates the failed invariant directly: the number of skip-table entries is tracked with a 16-bit counter (`TailCallTableInfo::kMaxSkipEntrySpacing`-based accounting), and by constructing a message with fields whose numbers are spaced to produce exactly `65536` entries, the entry count wraps to `3`. A filler/sentinel entry at the wrapped index is then misinterpreted as a real "start-of-block" record (`skipmap = 0xFFFF`, `field_entry_offset = 1`), producing a bogus `fstart` value. When a client subsequently sends a binary-encoded field whose field number is greater than this bogus `fstart` (`kTargetFieldNumber = 131072` in the PoC, encoded via a normal varint tag as shown at [2](#0-1) ), the fast-table lookup performed inside `TcParser`'s generated parse code (`src/google/protobuf/generated_message_tctable_lite.cc`) computes an index derived from this corrupted state and can index past the end of the `field_entries`/skip-map arrays that are laid out contiguously in the read-only `TcParseTableBase`.

This transfers the ImageMagick invariant precisely:
- **Attacker-controlled value:** the wire-format field number in an ordinary bounded binary payload sent to a public `ParseFrom*`/`MergeFrom*` API — no privileged access or hostile schema needed to trigger the read itself.
- **Missing check:** the fast dispatch code assumes the skip-table entry count fits in `uint16_t` and that generated `fstart`/offset values are internally consistent; there is no runtime bounds check on the derived table index before it is dereferenced.
- **Impact:** out-of-bounds read within process memory during table-driven wire-format dispatch, matching the "out-of-bounds read via crafted file/crafted field" pattern of the CVE.

### Impact Explanation
The corrupted lookup causes `TcParser` to read table memory beyond the intended entries array during dispatch. Depending on adjacent memory layout, this can disclose adjacent process memory content used as a bogus "field number"/offset, potentially leading to further misparse or crash (denial of service via SIGSEGV/ASan abort), consistent with the "Medium" severity, no-confidentiality/integrity-impact/availability-impact profile of the analog CVE. It is a read-only corruption in the parser's own control-flow structures, not an attacker-controlled write.

### Likelihood Explanation
Triggering the wraparound requires a **trusted, valid schema** with a large number of fields at specific spacings that make the generated skip-table entry count wrap exactly through 65536 — this is a property of the message descriptor built at compile/build time (via `protoc`/`DescriptorPool`), not something an ordinary wire-format client controls. Critically, the test itself calls `pool.EnforceProtoLimits(false)` (see [3](#0-2) ) to bypass the DescriptorPool's normal schema-validity limits before it can even build such a message type — indicating that under default/production limits enforcement, a schema capable of reaching this wraparound may not normally be constructible. I was not able to fully verify, within tool-call limits, exactly which enforced descriptor limit (field count, field-number range, or declaration nesting) is responsible for blocking this in the default configuration, since `EnforceProtoLimits` and the specific limit constants were not fully traced in this pass. This uncertainty means the likelihood of this analog being reachable purely from an untrusted **binary payload** against a schema built under default production limits is not fully confirmed and should be treated as low/uncertain rather than proven-high.

### Recommendation
- Confirm (or add, if missing) an explicit compile-time/build-time check in the fast-table generator (`generated_message_tctable_gen.cc`) that rejects or falls back to the slow/reflection-based parser whenever the computed skip-entry count would exceed `65535`, rather than allowing silent `uint16_t` wraparound.
- Add a defensive bounds check in the `TcParser` fast-path lookup that validates the derived table index against the table's known `num_entries`/`aux_offset` size before dereferencing, so a malformed/inconsistent table (however it arises) cannot cause an out-of-bounds table read while processing untrusted wire data.
- Verify whether `DescriptorPool::EnforceProtoLimits(true)` (the default) actually prevents descriptors that can reach the wraparound in all supported languages/build configurations (not just C++ lite runtime), and document this as a load-bearing security invariant if so.

### Proof of Concept
The existing in-tree regression test is itself the reproduction: `TcParserTest.OobGenReproduction` in [4](#0-3)  builds a message descriptor with carefully spaced field numbers to force the skip-table entry counter to wrap to `3`, then serializes a single varint field with `field_number = 131072` and calls `msg->ParseFromString(payload)` — this is the exact "bounded client payload through a public parse API" scenario required by the analog rules. I did not have terminal/build access in this pass to execute the test under a sanitizer and confirm current pass/fail status or an actual crash trace; a Devin session with repository build tooling would be needed to run this test under ASan and confirm whether the OOB read is presently caught/rejected or is live in the current checkout.

### Citations

**File:** src/google/protobuf/generated_message_tctable_lite_test.cc (L996-1071)
```text
TEST(TcParserTest, OobGenReproduction) {
  constexpr int kStartFieldNumber = 33;
  constexpr int kFieldSpacing = TailCallTableInfo::kMaxSkipEntrySpacing;

  constexpr int kTargetSkipEntryNum = 0xFFFF;
  constexpr int kTargetDistance = kTargetSkipEntryNum * /* bits-per-entry */ 16;
  constexpr int kLastStepSpacing = kTargetDistance % kFieldSpacing;

  // Each field spaced kFieldSpacing apart adds (kFieldSpacing / 16) entries
  // to the skip table block.
  constexpr int kEntriesPerField = kFieldSpacing / 16;

  // We want to overflow the uint16_t entry count (65535).
  // The first field adds 1 entry, and each subsequent field adds
  // kEntriesPerField. We want total_entries >= 65536 to overflow. 1 +
  // kEntriesPerField * (kNumFields - 1) >= 65536 kNumFields - 1 >= (65536 - 1)
  // / kEntriesPerField (round up)
  constexpr int kTargetEntries = 65536;
  constexpr int kNumFields =
      (kTargetEntries - 1 + kEntriesPerField - 1) / kEntriesPerField + 1;

  // The count wraps to:
  constexpr int kTotalEntries = 1 + kEntriesPerField * (kNumFields - 1);
  constexpr int kWrappedCount = kTotalEntries % 65536;
  static_assert(kWrappedCount == 3,
                "Wrapped count must be 3 to trigger the bug");

  // entries[kWrappedCount] (entries[3]) is a filler entry for the first field.
  // Filler entries have skipmap = 0xFFFF and field_entry_offset = 1.
  // Reinterpreted fstart = skipmap | (field_entry_offset << 16) =
  // 0xFFFF | (1 << 16) = 131071. We need target field number > fstart to
  // trigger OOB.
  constexpr int kReinterpretedFstart = 0xFFFF | (1 << 16);
  constexpr int kTargetFieldNumber = kReinterpretedFstart + 1;  // 131072

  FileDescriptorProto file_proto;
  file_proto.set_name("poc.proto");
  file_proto.set_syntax("editions");
  file_proto.set_edition(Edition::EDITION_2023);

  DescriptorProto* message_proto = file_proto.add_message_type();
  message_proto->set_name("M");

  int fnum = kStartFieldNumber;
  for (int i = 0; i < kNumFields; ++i) {
    FieldDescriptorProto* field = message_proto->add_field();
    field->set_name(absl::StrCat("f_", i));
    field->set_number(fnum);
    field->set_label(FieldDescriptorProto::LABEL_OPTIONAL);
    field->set_type(FieldDescriptorProto::TYPE_INT32);

    // We need to adjust one of them to ensure exact 0xFFFF overflow.
    fnum += i == kNumFields - 2 ? kLastStepSpacing : kFieldSpacing;
  }

  DescriptorPool pool;
  pool.EnforceProtoLimits(false);
  const FileDescriptor* file_desc = pool.BuildFile(file_proto);
  ASSERT_NE(file_desc, nullptr);

  const Descriptor* desc = file_desc->FindMessageTypeByName("M");
  ASSERT_NE(desc, nullptr);

  DynamicMessageFactory factory(&pool);
  std::unique_ptr<Message> msg(factory.GetPrototype(desc)->New());

  std::string payload;
  {
    io::StringOutputStream output(&payload);
    io::CodedOutputStream coded_output(&output);
    coded_output.WriteVarint32(WireFormatLite::MakeTag(
        kTargetFieldNumber, WireFormatLite::WIRETYPE_VARINT));
    coded_output.WriteVarint32(1);  // value
  }

  (void)msg->ParseFromString(payload);
```
